from pathlib import Path

import pytest

from hamilton_flow import builder
from hamilton_flow.nodes import load_manifest

EXAMPLES = Path(__file__).parents[1] / "examples"

SPEC = {
    "semantic_models": [
        {
            "name": "orders", "table": "main.orders",
            "entities": [{"name": "order", "type": "primary", "expr": "order_id"}, {"name": "customer", "type": "foreign", "expr": "customer_id"}],
            "dimensions": [{"name": "ordered_at", "type": "time", "granularity": "day"}, {"name": "status", "type": "categorical"}],
            "measures": [{"name": "order_amount", "agg": "sum", "expr": "amount"}, {"name": "order_count", "agg": "count_distinct", "expr": "order_id"}],
            "agg_time_dimension": "ordered_at",
        },
        {"name": "customers", "table": "main.customers",
         "entities": [{"name": "customer", "type": "primary", "expr": "customer_id"}],
         "dimensions": [{"name": "country", "type": "categorical"}]},
    ],
    "metrics": [
        {"name": "revenue", "type": "simple", "measure": "order_amount"},
        {"name": "orders", "type": "simple", "measure": "order_count"},
        {"name": "aov", "type": "ratio", "numerator": "revenue", "denominator": "orders"},
        {"name": "aov_after_fee", "type": "derived", "expr": "aov * 0.9", "inputs": [{"name": "aov"}]},
        {"name": "revenue_growth", "type": "derived", "expr": "revenue - prior", "inputs": [{"name": "revenue"}, {"name": "revenue", "alias": "prior", "offset_window": "1 day"}]},
        {"name": "running", "type": "cumulative", "measure": "order_amount"},
        {"name": "paid_revenue", "type": "simple", "measure": "order_amount", "filter": "{{ Dimension('order__status') }} = 'paid'"},
    ],
}


def test_valid_spec_has_no_issues():
    assert builder.check(SPEC) == []


def test_metric_on_metric_on_metric_executes(tmp_path):
    import duckdb

    from hamilton_flow import HamiltonFlow
    from hamilton_flow.client import DuckDBClient
    from hamilton_flow.requests import request_from_dict

    builder.save(tmp_path, SPEC)
    with duckdb.connect() as c:
        c.execute((EXAMPLES / "setup.sql").read_text())
        flow = HamiltonFlow(tmp_path, DuckDBClient(c))
        rows = flow.query(request_from_dict({"metric_names": ["aov_after_fee", "paid_revenue"], "group_by_names": ["customer__country"], "order_by_names": ["customer__country"]})).rows
    assert [(r[0], round(float(r[1]), 4), float(r[2])) for r in rows] == [("CH", 45.0, 150.0), ("DE", 180.0, 200.0)]


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s["metrics"].append({"name": "Bad Name", "type": "simple", "measure": "order_amount"}), "metrics[7].name"),
    (lambda s: s["metrics"].append({"name": "x", "type": "ratio", "numerator": "nope", "denominator": "revenue"}), "numerator"),
    (lambda s: s["metrics"].append({"name": "y", "type": "simple", "measure": "nope"}), "measure"),
    (lambda s: s["metrics"].append({"name": "z", "type": "derived", "expr": "", "inputs": []}), "expr"),
    (lambda s: s["semantic_models"][0].update(table="bad table"), "table"),
    (lambda s: s["semantic_models"][0].update(agg_time_dimension="status"), "agg_time_dimension"),
    (lambda s: s["metrics"].append({"name": "revenue", "type": "simple", "measure": "order_amount"}), ""),
])
def test_invalid_specs_are_reported(mutate, needle):
    import copy
    spec = copy.deepcopy(SPEC)
    mutate(spec)
    issues = builder.check(spec)
    assert issues and issues[0]["level"] == "error" and needle in issues[0]["message"]


def test_save_refuses_invalid_and_keeps_previous(tmp_path):
    builder.save(tmp_path, SPEC)
    bad = {**SPEC, "metrics": SPEC["metrics"] + [{"name": "q", "type": "ratio", "numerator": "q", "denominator": "revenue"}]}
    assert builder.save(tmp_path, bad)[0]["level"] == "error"
    assert len(builder.load_spec(tmp_path)["metrics"]) == len(SPEC["metrics"])


def test_existing_manifest_round_trips_through_spec():
    spec = builder.spec_from_manifest(load_manifest(str(EXAMPLES / "semantic")))
    assert {m["name"] for m in spec["metrics"]} >= {"revenue", "average_order_value", "revenue_after_fee"}
    assert builder.check(spec) == []
