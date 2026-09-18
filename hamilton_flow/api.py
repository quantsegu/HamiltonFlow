from __future__ import annotations

import logging
from pathlib import Path

from hamilton import driver
from metricflow.data_table.mf_table import MetricFlowDataTable
from metricflow.engine.metricflow_engine import (
    MetricFlowExplainResult,
    MetricFlowQueryRequest,
)
from metricflow.protocols.sql_client import SqlClient

from . import nodes

logger = logging.getLogger(__name__)


class HamiltonFlow:
    """Run complete native MetricFlow requests through an inspectable Hamilton DAG.

    A session owns a Hamilton driver, not the supplied SQL connection. It is
    synchronous; create separate sessions/clients for concurrent work.
    """

    def __init__(self, manifest_path: str | Path, sql_client: SqlClient) -> None:
        self.manifest_path = str(Path(manifest_path).resolve())
        self.sql_client = sql_client
        self.driver = driver.Builder().with_modules(nodes).build()

    def explain(self, request: MetricFlowQueryRequest) -> MetricFlowExplainResult:
        return self.driver.execute(["compiled_query"], inputs=self._inputs(request))[
            "compiled_query"
        ]

    def query(self, request: MetricFlowQueryRequest) -> MetricFlowDataTable:
        return self.driver.execute(["query_result"], inputs=self._inputs(request))[
            "query_result"
        ]

    def _inputs(self, request: MetricFlowQueryRequest) -> dict:
        return {
            "manifest_path": self.manifest_path,
            "sql_client": self.sql_client,
            "query_request": request,
        }

    def catalog(self) -> dict:
        manifest = nodes.semantic_manifest(self.manifest_path)
        return {
            "metrics": [m.name for m in manifest.metrics],
            "semantic_models": [m.name for m in manifest.semantic_models],
            "saved_queries": [q.name for q in manifest.saved_queries],
        }
