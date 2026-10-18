import json
from pathlib import Path

import duckdb
import jsonschema
import pytest
import yaml
from hamilton_flow import HamiltonFlow
from hamilton_flow.client import DuckDBClient
from hamilton_flow.interchange import from_lookml, from_ossie, to_ossie
from hamilton_flow.nodes import load_manifest
from hamilton_flow.requests import request_from_dict

DATA = Path(__file__).parent / "data"
EXAMPLES = Path(__file__).parents[1] / "examples"
SCHEMA = json.loads((DATA / "ossie/ossie-schema.json").read_text())


@pytest.fixture
def connection():
    with duckdb.connect() as c:
        c.execute((EXAMPLES / "setup.sql").read_text())
        yield c




def query(conversion, connection, tmp_path, **request):
    conversion.write(tmp_path)
    flow = HamiltonFlow(tmp_path, DuckDBClient(connection))
    return flow.query(request_from_dict(request))


def test_schema_accepts_official_example():
    document = yaml.safe_load((DATA / "ossie/tpcds_semantic_model.yaml").read_text())
    jsonschema.validate(document, SCHEMA)


def test_ossie_import_executes(connection, tmp_path):
    conversion = from_ossie(DATA / "ossie/sales.ossie.yaml")
    conversion.validate()
    rows = query(
        conversion, connection, tmp_path,
        metric_names=["revenue", "orders_placed", "avg_ticket"],
        group_by_names=["customers__country"],
        order_by_names=["customers__country"],
    ).rows
    assert [tuple(float(v) if i else v for i, v in enumerate(r)) for r in rows] == [
        ("CH", 150.0, 3.0, 50.0),
        ("DE", 200.0, 1.0, 200.0),
    ]
    reasons = {u["name"]: u["reason"] for u in conversion.report.unsupported}
    assert reasons["running"] == "window function"


def test_ossie_tpcds_reports_gaps_instead_of_guessing():
    report = from_ossie(DATA / "ossie/tpcds_semantic_model.yaml").report
    reasons = " ".join(u["reason"] for u in report.unsupported)
    assert "composite key" in reasons and "no time field" in reasons and "window function" in reasons
    assert "dataset:store_sales" in report.converted


def test_ossie_export_is_schema_valid_and_round_trips(connection, tmp_path):
    manifest = load_manifest(str(EXAMPLES / "semantic"))
    document, report = to_ossie(manifest)
    jsonschema.validate(document, SCHEMA)
    names = {m["name"] for m in document["metrics"]}
    assert {"revenue", "orders", "average_order_value"} <= names
    skipped = {u["name"] for u in report.unsupported}
    assert "running_revenue" in skipped
    back = from_ossie(document)
    back.validate()
    original = HamiltonFlow(EXAMPLES / "semantic", DuckDBClient(connection)).query(
        request_from_dict({"metric_names": ["revenue"], "group_by_names": ["customer__country"], "order_by_names": ["customer__country"]})
    ).rows
    again = query(
        back, connection, tmp_path,
        metric_names=["revenue"], group_by_names=["customers__country"], order_by_names=["customers__country"],
    ).rows
    assert [tuple(r) for r in again] == [tuple(r) for r in original]


def test_lookml_import_executes_and_reports(connection, tmp_path):
    conversion = from_lookml(DATA / "sales.lkml")
    conversion.validate()
    rows = query(
        conversion, connection, tmp_path,
        metric_names=["revenue", "buyers"], group_by_names=["customers__country"],
        order_by_names=["customers__country"],
    ).rows
    assert [(r[0], float(r[1]), int(r[2])) for r in rows] == [("CH", 150.0, 2), ("DE", 200.0, 1)]
    reasons = " ".join(f"{u['name']}:{u['reason']}" for u in conversion.report.unsupported)
    for expected in ("liquid", "filtered measure", "type: number", "derived_table"):
        assert expected in reasons
