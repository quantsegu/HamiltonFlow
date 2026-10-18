"""Live ClickHouse parity: same requests on DuckDB and ClickHouse must agree.

Skipped unless CLICKHOUSE_HOST is set (CI provides a service container).
"""

import os
from pathlib import Path

import duckdb
import pytest

from hamilton_flow import HamiltonFlow
from hamilton_flow.client import DuckDBClient
from hamilton_flow.requests import request_from_dict
from hamilton_flow.warehouse_client import WarehouseClient

EXAMPLES = Path(__file__).parents[2] / "examples"
pytestmark = pytest.mark.skipif(
    not os.environ.get("CLICKHOUSE_HOST"), reason="CLICKHOUSE_HOST not set"
)

SETUP = [
    "CREATE DATABASE IF NOT EXISTS main",
    "DROP TABLE IF EXISTS main.orders",
    "DROP TABLE IF EXISTS main.customers",
    "DROP TABLE IF EXISTS main.time_spine",
    "CREATE TABLE main.orders (order_id Int64, customer_id Int64, ordered_at DateTime, amount Float64, status String) ENGINE = MergeTree ORDER BY order_id",
    "INSERT INTO main.orders VALUES (1,10,'2026-01-01 00:00:00',100.0,'paid'),(2,10,'2026-01-02 00:00:00',50.0,'paid'),(3,20,'2026-01-02 00:00:00',200.0,'paid'),(4,30,'2026-01-03 00:00:00',0.0,'cancelled')",
    "CREATE TABLE main.customers (customer_id Int64, country String) ENGINE = MergeTree ORDER BY customer_id",
    "INSERT INTO main.customers VALUES (10,'CH'),(20,'DE'),(30,'CH')",
    "CREATE TABLE main.time_spine (date_day Date) ENGINE = MergeTree ORDER BY date_day",
    "INSERT INTO main.time_spine SELECT toDate('2026-01-01') + number FROM numbers(5)",
]

REQUESTS = [
    {"metric_names": ["revenue", "orders", "buyers"]},
    {"metric_names": ["revenue", "orders", "average_order_value"], "group_by_names": ["customer__country"], "order_by_names": ["customer__country"]},
    {"metric_names": ["revenue"], "group_by_names": ["order__status"], "where_constraints": ["{{ Dimension('order__status') }} = 'paid'"]},
    {"metric_names": ["revenue"], "group_by_names": ["metric_time__day"], "order_by_names": ["metric_time__day"]},
    {"metric_names": ["revenue_after_fee"], "group_by_names": ["metric_time__month"]},
    {"metric_names": ["running_revenue"], "group_by_names": ["metric_time__day"], "order_by_names": ["metric_time__day"]},
]


@pytest.fixture(scope="module")
def clients():
    os.environ.setdefault("CLICKHOUSE_USER", "default")
    os.environ.setdefault("CLICKHOUSE_PASSWORD", "")
    config = {
        "type": "clickhouse",
        "connection": {
            "host": {"env": "CLICKHOUSE_HOST"},
            "port": int(os.environ.get("CLICKHOUSE_PORT", "8123")),
            "username": {"env": "CLICKHOUSE_USER"},
            "password": {"env": "CLICKHOUSE_PASSWORD"},
            "secure": False,
        },
    }
    remote = WarehouseClient(config)
    for statement in SETUP:
        remote.connection.execute(statement)
    local = duckdb.connect()
    local.execute((EXAMPLES / "setup.sql").read_text())
    yield DuckDBClient(local), remote
    remote.close()
    local.close()


def normalise(rows):
    return [
        tuple(round(float(v), 6) if isinstance(v, (int, float)) or hasattr(v, "as_tuple") else str(v) for v in row)
        for row in rows
    ]


@pytest.mark.parametrize("payload", REQUESTS, ids=lambda p: "+".join(p["metric_names"]) + str(p.get("group_by_names", "")))
def test_matches_duckdb(clients, payload):
    local, remote = clients
    expected = HamiltonFlow(EXAMPLES / "semantic", local).query(request_from_dict(payload))
    actual = HamiltonFlow(EXAMPLES / "semantic", remote).query(request_from_dict(payload))
    assert list(actual.columns) == list(expected.columns)
    assert normalise(actual.rows) == normalise(expected.rows)
