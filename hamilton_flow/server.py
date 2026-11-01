"""Agent- and BI-friendly serving layer: one service, two transports (HTTP, MCP).

Tools: ``catalog`` (what can I ask), ``explain`` (the SQL, no execution), ``query``.
Failures come back as structured ``{"error": {"kind", ...}}`` payloads so a caller
can tell a bad request from a bad manifest or a warehouse outage.
Requests are serialized with a lock because one session owns one SQL connection.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from . import builder, sources
from .api import HamiltonFlow
from .errors import HamiltonFlowError, RequestError, WarehouseError
from .requests import request_from_dict

UI_FILE = Path(__file__).parent / "ui" / "index.html"
try:  # FastAPI is an optional extra; the annotation must resolve at module level
    from fastapi import Request
except ImportError:  # pragma: no cover
    Request = None

_STATUS = {"request": 422, "manifest": 500, "warehouse": 502, "result": 500}


class Service:
    def __init__(self, flow: HamiltonFlow, max_rows: int = 10_000, project: Path | None = None) -> None:
        self.flow = flow
        self.project = project
        self.max_rows = max_rows
        self._lock = threading.Lock()

    def _guard(self, fn):
        with self._lock:
            try:
                return fn(), 200
            except HamiltonFlowError as exc:
                return exc.to_dict(), _STATUS.get(exc.kind, 500)

    def catalog(self) -> tuple[dict, int]:
        return self._guard(self.flow.catalog)

    def explorer_catalog(self) -> tuple[dict, int]:
        def run():
            if self.project is not None and not (self.project / builder.YAML_FILE).exists():
                return {"metrics": [], "empty": True}
            return {"metrics": self.flow.explorer_catalog()}

        return self._guard(run)

    def explain(self, request: dict[str, Any]) -> tuple[dict, int]:
        def run():
            compiled = self.flow.compile(request_from_dict(request))
            return {
                "sql": compiled.sql,
                "dialect": compiled.dialect,
                "run": self.flow.last_run,
            }

        return self._guard(run)

    def query(self, request: dict[str, Any]) -> tuple[dict, int]:
        def run():
            result = self.flow.query(request_from_dict(request))
            rows = result.to_records()
            truncated = len(rows) > self.max_rows
            return {
                "columns": list(result.columns),
                "rows": rows[: self.max_rows],
                "row_count": result.row_count,
                "truncated": truncated,
                "sql": result.sql,
                "run": self.flow.last_run,
            }

        return self._guard(run)


def create_app(flow: HamiltonFlow, max_rows: int = 10_000, project: str | Path | None = None):
    from fastapi import FastAPI
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import HTMLResponse, JSONResponse

    project = Path(project).resolve() if project is not None else None
    service = Service(flow, max_rows, project)
    app = FastAPI(title="HamiltonFlow", version="0.2.0")

    def respond(pair):
        body, status = pair
        return JSONResponse(jsonable_encoder(body), status_code=status)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def ui():
        return HTMLResponse(UI_FILE.read_text(encoding="utf-8"))

    @app.get("/explorer/catalog")
    def explorer_catalog():
        return respond(service.explorer_catalog())

    @app.get("/catalog")
    def catalog():
        return respond(service.catalog())

    @app.post("/explain")
    def explain(request: dict[str, Any]):
        return respond(service.explain(request))

    @app.post("/query")
    def query(request: dict[str, Any]):
        return respond(service.query(request))

    @app.get("/lineage")
    def lineage():
        return flow.lineage()

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    @app.get("/ui/{name}", include_in_schema=False)
    def ui_asset(name: str):
        path = UI_FILE.parent / name
        if name not in {"common.js", "builder.js", "composer.js"} or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    if project is not None:
        _builder_routes(app, flow, service, project, respond)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


def _builder_routes(app, flow, service, project, respond):
    """Endpoints behind the Data / Metrics UI. Only mounted in --project mode."""

    def conn():
        return sources.connection_of(flow.sql_client)

    def apply():
        flow.manifest_path = str(project)

    def state():
        spec = builder.load_spec(project)
        if not spec["semantic_models"] and not (project / builder.SPEC_FILE).exists() and (project / builder.YAML_FILE).exists():
            spec = builder.spec_from_manifest(nodes_load(project))
        issues = builder.check(spec)
        yaml_text = ""
        if not any(i["level"] == "error" for i in issues):
            yaml_text = builder.to_yaml(builder.compile_documents(spec))
        return {
            "spec": spec,
            "tables": sources.list_tables(conn()),
            "issues": issues,
            "yaml": yaml_text,
            "options": {
                "aggregations": builder.AGGREGATIONS,
                "metric_types": builder.METRIC_TYPES,
                "grains": builder.GRAINS,
            },
        }

    def nodes_load(path):
        from .nodes import load_manifest

        return load_manifest(str(path))

    @app.get("/builder/state")
    def builder_state():
        return respond(service._guard(state))

    @app.post("/builder/check")
    def builder_check(payload: dict):
        def run():
            spec = payload.get("spec", {})
            issues = builder.check(spec)
            ok = not any(i["level"] == "error" for i in issues)
            return {"issues": issues, "yaml": builder.to_yaml(builder.compile_documents(spec)) if ok else ""}

        return respond(service._guard(run))

    @app.put("/builder/model")
    def builder_save(payload: dict):
        def run():
            spec = payload.get("spec", {})
            issues = builder.save(project, spec)
            saved = not any(i["level"] == "error" for i in issues) and bool(spec.get("metrics"))
            if saved:
                sources.ensure_time_spine(conn())
                apply()
            return {"saved": saved, "issues": issues}

        return respond(service._guard(run))

    @app.post("/builder/quick-metric")
    def builder_quick_metric(payload: dict):
        def run():
            request = payload.get("request", {})
            table = request.get("table", "")
            schema, _, name = table.rpartition(".")
            columns = sources.describe(conn(), schema or "main", name)
            return builder.quick_metric(payload.get("spec", {}), columns, request)

        return respond(service._guard(run))

    @app.post("/builder/reachable")
    def builder_reachable(payload: dict):
        def run():
            spec, measure = payload.get("spec", {}), payload.get("measure", "")
            if payload.get("model"):
                return {"dimensions": builder.reachable_dimensions(spec, payload["model"])}
            owner = next(
                (m["name"] for m in spec.get("semantic_models", []) if any(x.get("name") == measure for x in m.get("measures", []))),
                None,
            )
            return {"dimensions": builder.reachable_dimensions(spec, owner) if owner else []}

        return respond(service._guard(run))

    @app.post("/builder/preview")
    def builder_preview(payload: dict):
        def run():
            import tempfile

            spec = builder.apply_filter_rows(payload.get("spec", {}))
            documents = builder.compile_documents(spec)
            metric = payload.get("metric")
            names = [metric] if metric else [m["name"] for m in spec.get("metrics", [])][:1]
            sources.ensure_time_spine(conn())
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / builder.YAML_FILE).write_text(builder.to_yaml(documents))
                draft = HamiltonFlow(tmp, flow.sql_client)
                catalog = {m["name"]: m for m in draft.explorer_catalog()}
                dimensions = catalog.get(names[0], {}).get("dimensions", []) if names else []
                request = {"metric_names": names, "limit": min(int(payload.get("limit", 50)), 200)}
                if payload.get("group_by"):
                    request["group_by_names"] = [payload["group_by"]]
                    request["order_by_names"] = [payload["group_by"]]
                try:
                    result = draft.query(request_from_dict(request))
                except RequestError as exc:
                    return {"error": exc.message, "dimensions": dimensions, "columns": [], "rows": [], "sql": ""}
            records = result.to_records()
            return {
                "columns": list(result.columns),
                "rows": records,
                "sql": result.sql,
                "dimensions": dimensions,
            }

        return respond(service._guard(run))

    @app.post("/builder/suggest")
    def builder_suggest(payload: dict):
        def run():
            table = payload.get("table", "")
            schema, _, name = table.rpartition(".")
            return sources.suggest(table, sources.describe(conn(), schema or "main", name))

        return respond(service._guard(run))

    @app.get("/builder/tables/{schema}/{name}")
    def builder_table(schema: str, name: str):
        return respond(service._guard(lambda: sources.sample(conn(), schema, name)))

    @app.delete("/builder/tables/{schema}/{name}")
    def builder_drop(schema: str, name: str):
        return respond(service._guard(lambda: sources.drop_table(conn(), schema, name) or {"dropped": f"{schema}.{name}"}))

    @app.post("/builder/upload")
    async def builder_upload(request: Request, filename: str, table: str, replace: bool = False):
        body = await request.body()
        return respond(service._guard(lambda: sources.load_file(conn(), project, filename, body, table, replace)))


def create_mcp(flow: HamiltonFlow, max_rows: int = 1000):
    """MCP server (stdio or streamable HTTP) exposing catalog/explain/query tools."""
    from mcp.server.fastmcp import FastMCP

    service = Service(flow, max_rows)
    server = FastMCP("hamilton-flow")

    @server.tool()
    def catalog() -> dict:
        """List metrics, semantic models and saved queries that can be queried."""
        return service.catalog()[0]

    @server.tool()
    def explain(
        metric_names: list[str],
        group_by_names: list[str] | None = None,
        where_constraints: list[str] | None = None,
        order_by_names: list[str] | None = None,
        limit: int | None = None,
        time_constraint_start: str | None = None,
        time_constraint_end: str | None = None,
    ) -> dict:
        """Compile a metric query to SQL without running it."""
        return service.explain(_clean(locals()))[0]

    @server.tool()
    def query(
        metric_names: list[str],
        group_by_names: list[str] | None = None,
        where_constraints: list[str] | None = None,
        order_by_names: list[str] | None = None,
        limit: int | None = None,
        time_constraint_start: str | None = None,
        time_constraint_end: str | None = None,
    ) -> dict:
        """Run a metric query. Group by e.g. 'customer__country' or 'metric_time__month'."""
        return service.query(_clean(locals()))[0]

    return server


_REQUEST_KEYS = {
    "metric_names", "group_by_names", "where_constraints", "order_by_names",
    "limit", "time_constraint_start", "time_constraint_end",
}


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if k in _REQUEST_KEYS and v is not None}


__all__ = ["Service", "WarehouseError", "create_app", "create_mcp"]
