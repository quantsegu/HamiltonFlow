import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from hamilton_flow import HamiltonFlow, cli
from hamilton_flow.client import DuckDBClient
from hamilton_flow.server import Service, create_app, create_mcp

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.fixture
def flow():
    with duckdb.connect() as c:
        c.execute((EXAMPLES / "setup.sql").read_text())
        yield HamiltonFlow(EXAMPLES / "semantic", DuckDBClient(c), cache=True)


def test_http_endpoints(flow):
    client = TestClient(create_app(flow))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "revenue" in client.get("/catalog").json()["metrics"]
    body = {"metric_names": ["revenue"], "group_by_names": ["customer__country"], "order_by_names": ["customer__country"]}
    assert "SELECT" in client.post("/explain", json=body).json()["sql"].upper()
    result = client.post("/query", json=body).json()
    assert result["rows"][0] == {"customer__country": "CH", "revenue": 150.0}
    assert any(n["stage"] == "execute" for n in client.get("/lineage").json())


def test_http_errors_are_structured(flow):
    client = TestClient(create_app(flow))
    bad = client.post("/query", json={"metric_names": ["nope"]})
    assert bad.status_code == 422 and bad.json()["error"]["kind"] == "request"
    bad = client.post("/query", json={"metric_names": ["revenue"], "limit": "x"})
    assert bad.status_code == 422 and "limit" in bad.json()["error"]["message"]


def test_row_cap(flow):
    out, _ = Service(flow, max_rows=1).query({"metric_names": ["orders"], "group_by_names": ["order__status"]})
    assert out["truncated"] and len(out["rows"]) == 1 and out["row_count"] == 2


def test_mcp_tools_registered(flow):
    import asyncio

    server = create_mcp(flow)
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert tools == {"catalog", "explain", "query"}
    content = asyncio.run(server.call_tool("query", {"metric_names": ["orders"]}))
    assert "row_count" in json.dumps(content, default=str)


def test_cli_query_and_error_exit_code(tmp_path, capsys):
    db = tmp_path / "d.duckdb"
    c = duckdb.connect(str(db))
    c.execute((EXAMPLES / "setup.sql").read_text())
    c.close()
    base = ["--manifest", str(EXAMPLES / "semantic"), "--database", str(db)]
    cli.main(["query", *base, "--request", str(EXAMPLES / "query.json")])
    assert json.loads(capsys.readouterr().out)["rows"][0][0] == "CH"
    bad = tmp_path / "bad.json"
    bad.write_text('{"metric_names": ["nope"]}')
    with pytest.raises(SystemExit) as info:
        cli.main(["query", *base, "--request", str(bad)])
    assert info.value.code == 2
    assert json.loads(capsys.readouterr().err)["error"]["kind"] == "request"


def test_cli_interchange(tmp_path, capsys):
    data = Path(__file__).parent / "data"
    out = tmp_path / "out"
    cli.main(["import-ossie", "--input", str(data / "ossie/sales.ossie.yaml"), "--output", str(out)])
    assert (out / "converted.yaml").exists()
    assert "running" in capsys.readouterr().out
    cli.main(["export-ossie", "--manifest", str(EXAMPLES / "semantic"), "--output", str(tmp_path / "o.yaml")])
    assert "version:" in (tmp_path / "o.yaml").read_text()


def test_explorer_ui_and_catalog(flow):
    client = TestClient(create_app(flow))
    page = client.get("/")
    assert page.status_code == 200 and "HamiltonFlow Explorer" in page.text
    metrics = {m["name"]: m for m in client.get("/explorer/catalog").json()["metrics"]}
    assert "customer__country" in {d["name"] for d in metrics["revenue"]["dimensions"]}
    assert metrics["average_order_value"]["type"] == "ratio"


def test_run_info_reports_cache_and_node_timings(flow):
    client = TestClient(create_app(flow))
    body = {"metric_names": ["revenue"]}
    first = client.post("/query", json=body).json()["run"]
    second = client.post("/query", json=body).json()["run"]
    assert (first["cache"], second["cache"]) == ("miss", "hit")
    ran_first = {n["node"] for n in first["nodes"]}
    ran_second = {n["node"] for n in second["nodes"]}
    assert {"parsed_manifest", "compiled_query", "raw_result"} <= ran_first
    assert "compiled_query" not in ran_second and "raw_result" in ran_second
    assert all(n["ms"] >= 0 for n in first["nodes"])
