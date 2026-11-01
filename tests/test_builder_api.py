import duckdb
import pytest
from fastapi.testclient import TestClient

from hamilton_flow import HamiltonFlow, sources
from hamilton_flow.client import DuckDBClient
from hamilton_flow.server import create_app

CSV = b"order_id,customer_id,ordered_at,amount,status\n1,10,2026-01-01,100.0,paid\n2,10,2026-01-02,50.0,paid\n3,20,2026-01-02,200.0,paid\n4,30,2026-01-03,0.0,cancelled\n"
CUSTOMERS = b"customer_id,country\n10,CH\n20,DE\n30,CH\n"


@pytest.fixture
def client(tmp_path):
    with duckdb.connect(str(tmp_path / "data.duckdb")) as c:
        sources.ensure_time_spine(c)
        flow = HamiltonFlow(tmp_path, DuckDBClient(c), cache=True)
        yield TestClient(create_app(flow, project=tmp_path))


def upload(client, name, content, filename=None, replace=False):
    return client.post(f"/builder/upload?filename={filename or name + '.csv'}&table={name}&replace={str(replace).lower()}", content=content)


def test_empty_project_state_and_catalog(client):
    state = client.get("/builder/state").json()
    assert state["spec"] == {"semantic_models": [], "metrics": []} and state["tables"] == []
    assert client.get("/explorer/catalog").json() == {"metrics": [], "empty": True}


def test_full_flow_upload_suggest_model_metrics_query(client):
    assert upload(client, "orders", CSV).status_code == 200
    assert upload(client, "customers", CUSTOMERS).status_code == 200
    tables = {t["name"] for t in client.get("/builder/state").json()["tables"]}
    assert tables == {"orders", "customers"}
    sample = client.get("/builder/tables/main/orders").json()
    assert sample["row_count"] == 4 and sample["columns"][0] == "order_id"

    spec = {"semantic_models": [], "metrics": []}
    for table in ("orders", "customers"):
        draft = client.post("/builder/suggest", json={"table": f"main.{table}"}).json()
        spec["semantic_models"].append(draft["semantic_model"])
        spec["metrics"] += draft["metrics"]
    orders = spec["semantic_models"][0]
    assert {"name": "order", "type": "primary", "expr": "order_id"} in orders["entities"]
    assert {"name": "customer", "type": "foreign", "expr": "customer_id"} in orders["entities"]
    assert orders["agg_time_dimension"] == "ordered_at"
    spec["metrics"] = [m for m in spec["metrics"] if m["name"] in {"amount", "orders_count"}]
    spec["metrics"] += [
        {"name": "avg_order_value", "type": "ratio", "numerator": "amount", "denominator": "orders_count"},
        {"name": "aov_net", "type": "derived", "expr": "avg_order_value * 0.9", "inputs": [{"name": "avg_order_value"}]},
    ]
    checked = client.post("/builder/check", json={"spec": spec}).json()
    assert checked["issues"] == [] and "aov_net" in checked["yaml"]
    saved = client.put("/builder/model", json={"spec": spec}).json()
    assert saved["saved"] is True

    catalog = {m["name"]: m for m in client.get("/explorer/catalog").json()["metrics"]}
    assert {"amount", "avg_order_value", "aov_net"} <= set(catalog)
    assert "customers__country" in {d["name"] for d in catalog["aov_net"]["dimensions"]} or "customer__country" in {d["name"] for d in catalog["aov_net"]["dimensions"]}
    group = next(d["name"] for d in catalog["aov_net"]["dimensions"] if d["name"].endswith("country"))
    result = client.post("/query", json={"metric_names": ["aov_net"], "group_by_names": [group], "order_by_names": [group]}).json()
    assert [round(r["aov_net"], 2) for r in result["rows"]] == [45.0, 180.0]


def test_invalid_model_is_not_saved(client):
    upload(client, "orders", CSV)
    bad = {"semantic_models": [], "metrics": [{"name": "xx", "type": "simple", "measure": "nope"}]}
    out = client.put("/builder/model", json={"spec": bad}).json()
    assert out["saved"] is False and out["issues"][0]["level"] == "error"
    assert client.get("/builder/state").json()["spec"]["metrics"] == []


def test_upload_validation(client):
    assert upload(client, "bad name", CSV).status_code == 422
    assert upload(client, "x", b"a", filename="x.exe").status_code == 422
    assert upload(client, "orders", CSV).status_code == 200
    again = upload(client, "orders", CSV)
    assert again.status_code == 422 and "replace" in again.json()["error"]["message"]
    assert upload(client, "orders", CSV, replace=True).status_code == 200
    assert upload(client, "broken", b"\x00\x01", filename="broken.parquet").status_code == 422


def test_drop_table(client):
    upload(client, "orders", CSV)
    assert client.delete("/builder/tables/main/orders").status_code == 200
    assert client.get("/builder/state").json()["tables"] == []


def test_builder_routes_absent_without_project():
    with duckdb.connect() as c:
        app = create_app(HamiltonFlow("examples/semantic", DuckDBClient(c)))
    assert TestClient(app).get("/builder/state").status_code == 404


def test_quick_metric_preview_and_reachable(client):
    upload(client, "orders", CSV)
    upload(client, "customers", CUSTOMERS)
    spec = {"semantic_models": [], "metrics": []}
    for table in ("orders", "customers"):
        draft = client.post("/builder/suggest", json={"table": f"main.{table}"}).json()
        spec["semantic_models"].append(draft["semantic_model"])
    made = client.post("/builder/quick-metric", json={"spec": spec, "request": {
        "table": "main.orders", "agg": "sum", "column": "amount", "name": "revenue",
        "filter_rows": [{"dimension": "order__status", "op": "eq", "value": "paid"}]}}).json()
    spec = made["spec"]
    assert made["measure"] == "total_amount" and "'paid'" in spec["metrics"][0]["filter"]
    names = {d["name"] for d in client.post("/builder/reachable", json={"spec": spec, "measure": "total_amount"}).json()["dimensions"]}
    assert {"order__status", "customer__country"} <= names
    prev = client.post("/builder/preview", json={"spec": spec, "metric": "revenue"}).json()
    assert prev["rows"] == [{"revenue": 350.0}]
    grouped = client.post("/builder/preview", json={"spec": spec, "metric": "revenue", "group_by": "customer__country"}).json()
    assert {r["customer__country"]: r["revenue"] for r in grouped["rows"]} == {"CH": 150.0, "DE": 200.0}
    assert any(d["name"] == "customer__country" for d in prev["dimensions"])
    # nothing was saved by previewing
    assert client.get("/builder/state").json()["spec"]["metrics"] == []


def test_preview_reports_errors(client):
    bad = client.post("/builder/preview", json={"spec": {"semantic_models": [], "metrics": [{"name": "xx", "type": "simple", "measure": "nope"}]}})
    assert bad.status_code == 422 and "measure" in bad.json()["error"]["message"]


def test_preview_returns_dimensions_even_when_query_is_invalid(client):
    upload(client, "orders", CSV)
    draft = client.post("/builder/suggest", json={"table": "main.orders"}).json()
    spec = {"semantic_models": [draft["semantic_model"]], "metrics": [
        {"name": "rows_n", "type": "simple", "measure": "orders_row_count"},
        {"name": "rows_growth", "type": "derived", "expr": "(rows_n - prior) / prior", "inputs": [{"name": "rows_n"}, {"name": "rows_n", "alias": "prior", "offset_window": "1 day"}]},
    ]}
    out = client.post("/builder/preview", json={"spec": spec, "metric": "rows_growth"}).json()
    assert "metric_time" in out["error"] and any(d["name"] == "metric_time__day" for d in out["dimensions"])
    ok = client.post("/builder/preview", json={"spec": spec, "metric": "rows_growth", "group_by": "metric_time__day"}).json()
    assert "error" not in ok and ok["columns"][0] == "metric_time__day"
