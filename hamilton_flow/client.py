from __future__ import annotations

import logging

import duckdb
from metricflow.data_table.mf_table import MetricFlowDataTable
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet

logger = logging.getLogger(__name__)
_EMPTY_PARAMETERS = SqlBindParameterSet()


class DuckDBClient:
    """MetricFlow SQL client on a caller-owned DuckDB connection."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection
        self.sql_engine_type = SqlEngine.DUCKDB
        self.sql_plan_renderer = DuckDbSqlPlanRenderer()

    def query(
        self,
        stmt: str,
        sql_bind_parameter_set: SqlBindParameterSet = _EMPTY_PARAMETERS,
    ) -> MetricFlowDataTable:
        cursor = self.connection.execute(stmt, sql_bind_parameter_set.param_dict)
        columns = tuple(item[0] for item in cursor.description)
        return MetricFlowDataTable.create_from_rows(columns, cursor.fetchall())

    def execute(
        self,
        stmt: str,
        sql_bind_parameter_set: SqlBindParameterSet = _EMPTY_PARAMETERS,
    ) -> None:
        self.connection.execute(stmt, sql_bind_parameter_set.param_dict)

    def dry_run(
        self,
        stmt: str,
        sql_bind_parameter_set: SqlBindParameterSet = _EMPTY_PARAMETERS,
    ) -> None:
        self.connection.execute("EXPLAIN " + stmt, sql_bind_parameter_set.param_dict)

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        return "$" + bind_parameter_key

    def close(self) -> None:
        """The caller owns the connection; do not close it here."""
