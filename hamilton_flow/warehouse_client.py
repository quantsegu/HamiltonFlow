"""Native MetricFlow renderers; ClickHouse uses an explicit DuckDB-to-ClickHouse bridge."""

from metricflow.data_table.mf_table import MetricFlowDataTable
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.databricks import DatabricksSqlPlanRenderer
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow.sql.render.snowflake import SnowflakeSqlPlanRenderer
from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet

from .warehouse import Connection, render

EMPTY = SqlBindParameterSet()


class WarehouseClient:
    def __init__(self, warehouse, connection=None):
        self.connection = connection or Connection(warehouse)
        self.engine = warehouse["type"]
        self.sql_engine_type = {
            "databricks": SqlEngine.DATABRICKS,
            "snowflake": SqlEngine.SNOWFLAKE,
        }.get(self.engine, SqlEngine.DUCKDB)
        self.sql_plan_renderer = {
            "databricks": DatabricksSqlPlanRenderer,
            "snowflake": SnowflakeSqlPlanRenderer,
        }.get(self.engine, DuckDbSqlPlanRenderer)()

    def prepare(self, sql, parameters=EMPTY):
        if parameters.param_items:
            raise ValueError(
                "Remote clients currently require resolved parameters; bound parameters are not supported"
            )
        return render(sql, "clickhouse") if self.engine == "clickhouse" else sql

    def query(self, sql, sql_bind_parameter_set=EMPTY):
        columns, rows = self.connection.query(self.prepare(sql, sql_bind_parameter_set))
        return MetricFlowDataTable.create_from_rows(tuple(columns), rows)

    def execute(self, sql, sql_bind_parameter_set=EMPTY):
        self.connection.execute(self.prepare(sql, sql_bind_parameter_set))

    def dry_run(self, sql, sql_bind_parameter_set=EMPTY):
        self.connection.query("EXPLAIN " + self.prepare(sql, sql_bind_parameter_set))

    def render_bind_parameter_key(self, bind_parameter_key):
        return "$" + bind_parameter_key

    def close(self):
        self.connection.close()
