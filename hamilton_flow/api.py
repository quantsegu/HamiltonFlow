from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hamilton import driver
from metricflow.engine.metricflow_engine import (
    MetricFlowQueryRequest,
)
from metricflow.protocols.sql_client import SqlClient
from metricflow_semantics.errors.error_classes import (
    InvalidQueryException,
    UnknownMetricError,
)

from . import nodes
from .cache import CompiledSqlCache
from .errors import HamiltonFlowError, RequestError
from .lineage import OpenLineageEmitter, Sink
from .nodes import CompiledSql, QueryResult

logger = logging.getLogger(__name__)

_REQUEST_ERRORS = (InvalidQueryException, UnknownMetricError)


class HamiltonFlow:
    """Run native MetricFlow requests through an inspectable, cached Hamilton DAG.

    Args:
        manifest_path: MetricFlow YAML directory or ``semantic_manifest.json``.
        sql_client: caller-owned MetricFlow SQL client (never closed here).
        cache: compiled-SQL cache. ``True`` for in-memory, a path for a
            persistent directory. Keyed on manifest content hash + request hash
            + SQL dialect; a hit skips parse/validate/plan/render.
        lineage_sink: callable receiving OpenLineage run events.

    Synchronous; create one session per client for concurrent work.
    """

    def __init__(
        self,
        manifest_path: str | Path,
        sql_client: SqlClient,
        *,
        cache: bool | str | Path = False,
        lineage_sink: Sink | None = None,
    ) -> None:
        self.manifest_path = str(Path(manifest_path).resolve())
        self.sql_client = sql_client
        self.sql_dialect = sql_client.sql_engine_type.value.lower()
        builder = driver.Builder().with_modules(nodes)
        if lineage_sink is not None:
            builder = builder.with_adapters(
                OpenLineageEmitter(lineage_sink, sql_dialect=self.sql_dialect)
            )
        self.driver = builder.build()
        self.cache = (
            None
            if cache is False
            else CompiledSqlCache(None if cache is True else cache)
        )

    def _run(self, target: str, request: MetricFlowQueryRequest) -> Any:
        inputs = self._inputs(request)
        overrides: dict[str, Any] = {}
        key = None
        if self.cache is not None and target in ("compiled_sql", "query_result"):
            fingerprints = self.driver.execute(
                ["manifest_fingerprint", "request_fingerprint"], inputs=inputs
            )
            key = CompiledSqlCache.key(
                fingerprints["manifest_fingerprint"],
                fingerprints["request_fingerprint"],
                self.sql_dialect,
            )
            hit = self.cache.get(key)
            if hit is not None:
                overrides["compiled_sql"] = hit
        try:
            if key is not None and not overrides:
                compiled = self.driver.execute(["compiled_sql"], inputs=inputs)
                self.cache.put(key, compiled["compiled_sql"])
                overrides["compiled_sql"] = compiled["compiled_sql"]
            return self.driver.execute([target], inputs=inputs, overrides=overrides)[
                target
            ]
        except HamiltonFlowError:
            raise
        except _REQUEST_ERRORS as exc:
            raise RequestError(str(exc)) from exc

    def explain(self, request: MetricFlowQueryRequest):
        return self._run("compiled_query", request)

    def compile(self, request: MetricFlowQueryRequest) -> CompiledSql:
        """Cached SQL artifact (skips parse/plan/render on a cache hit)."""
        return self._run("compiled_sql", request)

    def query(self, request: MetricFlowQueryRequest) -> QueryResult:
        return self._run("query_result", request)

    def _inputs(self, request: MetricFlowQueryRequest) -> dict:
        return {
            "manifest_path": self.manifest_path,
            "sql_client": self.sql_client,
            "query_request": request,
            "sql_dialect": self.sql_dialect,
        }

    def catalog(self) -> dict:
        manifest = nodes.load_manifest(self.manifest_path)
        return {
            "metrics": [m.name for m in manifest.metrics],
            "semantic_models": [m.name for m in manifest.semantic_models],
            "saved_queries": [q.name for q in manifest.saved_queries],
        }

    def lineage(self) -> list[dict]:
        """Static node-level lineage: each node, its stage tag and its inputs."""
        return [
            {
                "node": v.name,
                "stage": v.tags.get("stage"),
                "depends_on": list(v.required_dependencies)
                if hasattr(v, "required_dependencies")
                else [],
                "tags": dict(v.tags),
            }
            for v in self.driver.list_available_variables()
        ]
