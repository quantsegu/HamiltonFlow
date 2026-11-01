"""Data inputs for the Builder: list/describe/load tables and draft a semantic model.

Table loading and listing need a DuckDB connection (the project database). For
other warehouses the Builder can still model tables you name by hand.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import RequestError

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SUFFIXES = {".csv", ".tsv", ".parquet", ".json", ".jsonl", ".ndjson"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_NUMERIC = ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT")
_TIME = ("DATE", "TIMESTAMP", "DATETIME")


def ident(value: str, what: str = "name") -> str:
    if not isinstance(value, str) or not IDENT.match(value):
        raise RequestError(f"Invalid {what}: use letters, digits and underscores only.", details={"field": what})
    return value


def connection_of(client):
    connection = getattr(client, "connection", None)
    if connection is None or not hasattr(connection, "execute") or hasattr(connection, "query") and not hasattr(connection, "fetchall"):
        raise RequestError("Loading and listing tables needs a DuckDB project database.")
    return connection


def ensure_time_spine(connection) -> None:
    """MetricFlow needs a day-grain time spine table; create a wide default."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS main.time_spine AS "
        "SELECT CAST(d AS DATE) AS date_day FROM generate_series(DATE '2000-01-01', DATE '2040-12-31', INTERVAL 1 DAY) t(d)"
    )


def list_tables(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema','pg_catalog') ORDER BY 1,2"
    ).fetchall()
    out = []
    for schema, name in rows:
        if name == "time_spine":
            continue
        out.append({"schema": schema, "name": name, "table": f"{schema}.{name}", "columns": describe(connection, schema, name)})
    return out


def describe(connection, schema: str, name: str) -> list[dict[str, str]]:
    ident(schema, "schema"), ident(name, "table")
    rows = connection.execute(f'DESCRIBE "{schema}"."{name}"').fetchall()
    return [{"name": r[0], "type": r[1]} for r in rows]


def sample(connection, schema: str, name: str, limit: int = 5) -> dict[str, Any]:
    ident(schema, "schema"), ident(name, "table")
    cursor = connection.execute(f'SELECT * FROM "{schema}"."{name}" LIMIT {int(limit)}')
    columns = [c[0] for c in cursor.description]
    total = connection.execute(f'SELECT COUNT(*) FROM "{schema}"."{name}"').fetchone()[0]
    return {"columns": columns, "rows": [dict(zip(columns, map(_plain, r))) for r in cursor.fetchall()], "row_count": total}


def _plain(value):
    return value if isinstance(value, (int, float, str, bool, type(None))) else str(value)


def load_file(connection, project: Path, filename: str, content: bytes, table: str, replace: bool = False) -> dict:
    table = ident(table, "table")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise RequestError(f"Unsupported file type {suffix!r}; use one of {sorted(SUFFIXES)}.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise RequestError(f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    exists = connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='main' AND table_name=?", [table]
    ).fetchone()
    if exists and not replace:
        raise RequestError(f"Table main.{table} already exists; tick 'replace' to overwrite it.", details={"field": "table"})
    target = project / "data" / f"{table}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    reader = {".parquet": "read_parquet", ".json": "read_json_auto", ".jsonl": "read_json_auto", ".ndjson": "read_json_auto"}.get(suffix, "read_csv_auto")
    try:
        connection.execute(f'CREATE OR REPLACE TABLE main."{table}" AS SELECT * FROM {reader}(?)', [str(target)])
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise RequestError(f"Could not read {filename}: {exc}") from exc
    return {"table": f"main.{table}", "columns": describe(connection, "main", table)}


def drop_table(connection, schema: str, name: str) -> None:
    ident(schema, "schema"), ident(name, "table")
    connection.execute(f'DROP TABLE IF EXISTS "{schema}"."{name}"')


def _singular(name: str) -> str:
    return name[:-1] if name.endswith("s") and len(name) > 3 else name


def suggest(table: str, columns: list[dict[str, str]]) -> dict[str, Any]:
    """Draft a semantic model and simple metrics from column names and types."""
    base = table.split(".")[-1]
    model = _singular(base)
    entity_names = {f"{model}_id", "id", f"{base}_id"}
    sm: dict[str, Any] = {"name": base, "table": table, "entities": [], "dimensions": [], "measures": []}
    metrics: list[dict[str, Any]] = []
    for col in columns:
        name, kind = col["name"], col["type"].upper()
        safe = re.sub(r"[^a-z0-9_]", "_", name.lower())
        if not safe or not safe[0].isalpha():
            safe = "c_" + safe
        expr = name if safe != name else ""
        is_key = name.lower() in entity_names or name.lower().endswith(("_id", "_key"))
        if is_key:
            if name.lower() in entity_names and not any(e["type"] == "primary" for e in sm["entities"]):
                sm["entities"].append({"name": model, "type": "primary", "expr": name})
            else:
                sm["entities"].append({"name": re.sub(r"_(id|key)$", "", safe), "type": "foreign", "expr": name})
        elif kind.startswith(_TIME):
            sm["dimensions"].append({"name": safe, "type": "time", "granularity": "day", "expr": expr})
        elif kind == "BOOLEAN":
            sm["dimensions"].append({"name": safe, "type": "categorical", "expr": expr})
        elif kind.startswith(_NUMERIC):
            measure = f"total_{safe}"
            sm["measures"].append({"name": measure, "agg": "sum", "expr": name})
            metrics.append({"name": safe, "type": "simple", "measure": measure, "description": f"Sum of {name}"})
        else:
            sm["dimensions"].append({"name": safe, "type": "categorical", "expr": expr})
    if not any(e["type"] == "primary" for e in sm["entities"]):
        sm["primary_entity"] = base
    time_dims = [d["name"] for d in sm["dimensions"] if d["type"] == "time"]
    if sm["measures"]:
        sm["measures"].append({"name": f"{base}_row_count", "agg": "sum", "expr": "1"})
        metrics.append({"name": f"{base}_count", "type": "simple", "measure": f"{base}_row_count", "description": f"Number of rows in {base}"})
    if time_dims:
        sm["agg_time_dimension"] = time_dims[0]
    return {"semantic_model": sm, "metrics": metrics, "needs_time_dimension": bool(sm["measures"]) and not time_dims}
