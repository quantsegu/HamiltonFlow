from pathlib import Path

import duckdb
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from hamilton_flow import HamiltonFlow
from hamilton_flow.client import DuckDBClient
from hamilton_flow.errors import ManifestError, RequestError, WarehouseError
from hamilton_flow.lineage import tables_in_sql
from hamilton_flow.requests import request_from_dict

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.fixture
def connection():
    with duckdb.connect() as c:
        c.execute((EXAMPLES / "setup.sql").read_text())
        yield c


def make(connection, **kw):
    return HamiltonFlow(EXAMPLES / "semantic", DuckDBClient(connection), **kw)


def count_explains(monkeypatch):
    calls = []
    original = MetricFlowEngine.explain

    def counting(self, request):
        calls.append(1)
        return original(self, request)

    monkeypatch.setattr(MetricFlowEngine, "explain", counting)
    return calls


def test_cache_skips_planning(connection, monkeypatch):
    calls = count_explains(monkeypatch)
    flow = make(connection, cache=True)
    for _ in range(3):
        flow.query(request_from_dict({"metric_names": ["revenue"]}))
    assert len(calls) == 1 and flow.cache.hits == 2
    flow.query(request_from_dict({"metric_names": ["orders"]}))
    assert len(calls) == 2


def test_cache_persists_across_sessions(connection, tmp_path, monkeypatch):
    calls = count_explains(monkeypatch)
    request = {"metric_names": ["revenue"]}
    make(connection, cache=tmp_path).query(request_from_dict(request))
    make(connection, cache=tmp_path).query(request_from_dict(request))
    assert len(calls) == 1


def test_cache_invalidated_by_manifest_change(connection, tmp_path, monkeypatch):
    calls = count_explains(monkeypatch)
    manifest = tmp_path / "semantic"
    manifest.mkdir()
    (manifest / "sales.yaml").write_text((EXAMPLES / "semantic/sales.yaml").read_text())
    req = {"metric_names": ["revenue"]}
    cache = tmp_path / "cache"
    HamiltonFlow(manifest, DuckDBClient(connection), cache=cache).query(request_from_dict(req))
    (manifest / "sales.yaml").write_text(
        (EXAMPLES / "semantic/sales.yaml").read_text() + "\n# changed\n"
    )
    HamiltonFlow(manifest, DuckDBClient(connection), cache=cache).query(request_from_dict(req))
    assert len(calls) == 2


def test_stage_tags_cover_pipeline(connection):
    stages = {n["stage"] for n in make(connection).lineage()}
    assert {"ingest", "parse", "validate", "plan", "render", "execute", "check"} <= stages


def test_openlineage_events(connection):
    events = []
    make(connection, lineage_sink=events.append).query(
        request_from_dict({"metric_names": ["revenue"], "group_by_names": ["customer__country"]})
    )
    assert [e["eventType"] for e in events] == ["START", "COMPLETE"]
    inputs = {i["name"] for i in events[-1]["inputs"]}
    assert {"main.orders", "main.customers"} <= inputs
    assert events[0]["run"]["runId"] == events[1]["run"]["runId"]


def test_openlineage_fail_event(connection):
    events = []
    flow = make(connection, lineage_sink=events.append)
    with pytest.raises(RequestError):
        flow.query(MetricFlowQueryRequest.create(metric_names=["nope"]))
    assert events[-1]["eventType"] == "FAIL"


def test_tables_in_sql_excludes_ctes():
    sql = "WITH a AS (SELECT * FROM db.s.t) SELECT * FROM a JOIN db.s.u ON 1=1"
    assert tables_in_sql(sql) == ["db.s.t", "db.s.u"]


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"metric_names": ["revenue"], "time_constraint_start": "not-a-date"}, "time_constraint_start"),
        ({"metric_names": ["revenue"], "bogus": 1}, "bogus"),
        ({"metric_names": ["revenue"], "limit": -1}, "limit"),
        ({"metric_names": "revenue"}, "metric_names"),
    ],
)
def test_request_validation_messages(payload, field):
    with pytest.raises(RequestError) as info:
        request_from_dict(payload)
    assert field in info.value.message
    assert info.value.to_dict()["error"]["kind"] == "request"


def test_request_requires_metrics_and_ordered_times():
    with pytest.raises(RequestError):
        request_from_dict({})
    with pytest.raises(RequestError):
        request_from_dict(
            {
                "metric_names": ["revenue"],
                "time_constraint_start": "2026-02-01",
                "time_constraint_end": "2026-01-01",
            }
        )
    with pytest.raises(RequestError):
        request_from_dict([1])


def test_missing_manifest_is_manifest_error(connection, tmp_path):
    flow = HamiltonFlow(tmp_path / "missing", DuckDBClient(connection))
    with pytest.raises(ManifestError):
        flow.query(MetricFlowQueryRequest.create(metric_names=["revenue"]))


def test_invalid_manifest_is_manifest_error(connection, tmp_path):
    (tmp_path / "bad.yaml").write_text("semantic_model:\n  name: x\n")
    flow = HamiltonFlow(tmp_path, DuckDBClient(connection))
    with pytest.raises(ManifestError):
        flow.catalog()


def test_warehouse_error_is_wrapped(connection):
    flow = make(connection)
    connection.execute("DROP TABLE orders")
    with pytest.raises(WarehouseError) as info:
        flow.query(MetricFlowQueryRequest.create(metric_names=["revenue"]))
    assert "sql" in info.value.details


def test_result_records(connection):
    result = make(connection).query(
        request_from_dict({"metric_names": ["orders"], "group_by_names": ["customer__country"], "order_by_names": ["customer__country"]})
    )
    assert result.to_records()[0]["customer__country"] == "CH"


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    metrics=st.lists(
        st.sampled_from(["revenue", "orders", "buyers", "average_order_value", "revenue_after_fee"]),
        min_size=1, max_size=3, unique=True,
    ),
    groups=st.lists(
        st.sampled_from(["customer__country", "order__status", "metric_time__day", "metric_time__month"]),
        max_size=2, unique=True,
    ),
    limit=st.one_of(st.none(), st.integers(1, 5)),
)
def test_property_parity_with_direct_metricflow(connection, metrics, groups, limit):
    flow = make(connection, cache=True)
    request = MetricFlowQueryRequest.create(
        metric_names=metrics, group_by_names=groups, order_by_names=groups, limit=limit
    )
    from hamilton_flow.nodes import load_manifest

    direct = MetricFlowEngine(
        SemanticManifestLookup(load_manifest(flow.manifest_path)), flow.sql_client
    ).query(request).result_df
    actual = flow.query(request)
    assert [tuple(r) for r in actual.rows] == [tuple(r) for r in direct.rows]
    assert list(actual.columns) == [c.column_name for c in direct.column_descriptions]
