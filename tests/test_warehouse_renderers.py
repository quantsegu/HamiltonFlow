import json
from pathlib import Path

import pytest
import sqlglot
from hamilton_flow import HamiltonFlow
from hamilton_flow.warehouse import Connection
from hamilton_flow.warehouse_client import WarehouseClient
from metricflow.engine.metricflow_engine import MetricFlowQueryRequest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("engine", ["databricks", "snowflake", "clickhouse"])
def test_native_explain_and_clickhouse_translation_are_offline(engine, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("explain must not connect")

    monkeypatch.setattr(Connection, "connect", forbidden)
    warehouse = json.loads(
        (ROOT / "examples/warehouses" / f"{engine}.json").read_text()
    )
    client = WarehouseClient(warehouse)
    flow = HamiltonFlow(ROOT / "examples/semantic", client)
    statement = flow.explain(
        MetricFlowQueryRequest.create(
            metric_names=["revenue", "average_order_value"],
            group_by_names=["customer__country"],
        )
    ).sql_statement
    sql = client.prepare(statement.sql, statement.bind_parameter_set)
    sqlglot.parse_one(sql, read=engine)
    assert "JOIN" in sql.upper() and "SUM" in sql.upper()
