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

from .api import HamiltonFlow
from .errors import HamiltonFlowError, WarehouseError
from .requests import request_from_dict

UI_FILE = Path(__file__).parent / "ui" / "index.html"
_STATUS = {"request": 422, "manifest": 500, "warehouse": 502, "result": 500}


class Service:
    def __init__(self, flow: HamiltonFlow, max_rows: int = 10_000) -> None:
        self.flow = flow
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
        return self._guard(lambda: {"metrics": self.flow.explorer_catalog()})

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


def create_app(flow: HamiltonFlow, max_rows: int = 10_000):
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    service = Service(flow, max_rows)
    app = FastAPI(title="HamiltonFlow", version="0.2.0")

    def respond(pair):
        body, status = pair
        return JSONResponse(body, status_code=status)

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

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


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
