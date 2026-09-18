import json
from pathlib import Path

import duckdb
import pytest
from hamilton_flow import HamiltonFlow
from hamilton_flow.client import DuckDBClient
from hamilton_flow.nodes import semantic_manifest
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow_semantics.errors.error_classes import (
    InvalidQueryException,
    UnknownMetricError,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.fixture
def session():
    with duckdb.connect() as connection:
        connection.execute((EXAMPLES / "setup.sql").read_text())
        yield HamiltonFlow(EXAMPLES / "semantic", DuckDBClient(connection))


def test_hamilton_dag_is_real(session):
    names = {v.name for v in session.driver.list_available_variables()}
    assert {
        "semantic_manifest",
        "metric_engine",
        "compiled_query",
        "query_result",
    } <= names


@pytest.mark.parametrize(
    "metric,expected",
    [
        ("revenue", 350),
        ("orders", 4),
        ("buyers", 3),
        ("average_order_value", 87.5),
        ("revenue_after_fee", 315),
    ],
)
def test_metric_values(session, metric, expected):
    result = session.query(MetricFlowQueryRequest.create(metric_names=[metric]))
    assert float(result.rows[0][0]) == pytest.approx(expected)


def test_join_and_ratio(session):
    result = session.query(
        MetricFlowQueryRequest.create(
            **json.loads((EXAMPLES / "query.json").read_text())
        )
    )
    assert [tuple(str(v) for v in row) for row in result.rows] == [
        ("CH", "150.0", "3", "50.0"),
        ("DE", "200.0", "1", "200.0"),
    ]


def test_filter_and_order(session):
    request = MetricFlowQueryRequest.create(
        metric_names=["revenue"],
        group_by_names=["order__status"],
        where_constraints=["{{ Dimension('order__status') }} = 'paid'"],
        order_by_names=["-revenue"],
        limit=1,
    )
    result = session.query(request)
    assert result.rows[0][0] == "paid"
    assert float(result.rows[0][1]) == 350


def test_cumulative(session):
    result = session.query(
        MetricFlowQueryRequest.create(
            metric_names=["running_revenue"],
            group_by_names=["metric_time__day"],
            order_by_names=["metric_time__day"],
        )
    )
    assert [float(row[-1]) for row in result.rows][:3] == [100, 350, 350]


@pytest.mark.parametrize(
    "metrics,groups",
    [
        (["revenue"], []),
        (["average_order_value"], ["customer__country"]),
        (["running_revenue"], ["metric_time__day"]),
        (["revenue_after_fee", "buyers"], ["metric_time__month"]),
    ],
)
def test_same_as_direct_upstream(session, metrics, groups):
    native = MetricFlowEngine(
        SemanticManifestLookup(semantic_manifest(session.manifest_path)),
        session.sql_client,
    )
    request = MetricFlowQueryRequest.create(
        metric_names=metrics, group_by_names=groups, order_by_names=groups
    )
    direct = native.query(request).result_df
    actual = session.query(request)
    assert actual.rows == direct.rows
    assert actual.column_descriptions == direct.column_descriptions


def test_invalid_metric_rejected(session):
    with pytest.raises((InvalidQueryException, UnknownMetricError)):
        session.query(MetricFlowQueryRequest.create(metric_names=["does_not_exist"]))


def test_manifest_json_round_trip(session, tmp_path):
    manifest = semantic_manifest(session.manifest_path)
    path = tmp_path / "semantic_manifest.json"
    path.write_text(manifest.json())
    restored = HamiltonFlow(path, session.sql_client)
    assert (
        restored.query(MetricFlowQueryRequest.create(metric_names=["revenue"])).rows
        == session.query(MetricFlowQueryRequest.create(metric_names=["revenue"])).rows
    )


def test_explain_does_not_execute(session):
    session.sql_client.connection.execute("DROP TABLE orders")
    result = session.explain(MetricFlowQueryRequest.create(metric_names=["revenue"]))
    assert "orders" in result.sql_statement.sql


def test_catalog(session):
    assert "running_revenue" in session.catalog()["metrics"]


def test_json_time_constraints(session):
    from hamilton_flow.requests import request_from_dict

    request = request_from_dict(
        {
            "metric_names": ["revenue"],
            "time_constraint_start": "2026-01-02T00:00:00",
            "time_constraint_end": "2026-01-02T23:59:59",
        }
    )
    assert float(session.query(request).rows[0][0]) == 250
