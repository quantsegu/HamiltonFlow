# Warehouse capability

The new warehouse paths support configuration and SQL generation for Databricks, Snowflake, and ClickHouse. DuckDB remains the default for existing local examples. **Status:** DuckDB supported; ClickHouse is exercised live in CI (`tests/live`, parity against DuckDB); Snowflake and Databricks are **experimental** (offline SQL tests only, never run against a real account).

| Backend | Metric compilation | Execution client |
|---|---|---|
| Databricks | Native MetricFlow renderer | SQL Connector |
| Snowflake | Native MetricFlow renderer | Python Connector |
| ClickHouse | DuckDB MetricFlow renderer translated through SQLGlot; limited bridge | clickhouse-connect |

ClickHouse is not a native upstream MetricFlow backend. Unsupported translations fail rather than silently falling back. Use a modern ClickHouse release (25.8+ recommended) for correlated subqueries and window queries; this version floor has not been live certified. Do not assume complete MetricFlow feature parity. Remote MetricFlow bound parameters currently fail closed; use requests that compile to resolved SQL.

## Connection configuration

Copy the matching file from `examples/warehouses/`. Connection-only files contain `type` and `connection`. Vault and calculated metric specifications embed that object under `warehouse`.

```json
{"type":"snowflake","connection":{"account":{"env":"SNOWFLAKE_ACCOUNT"},"user":{"env":"SNOWFLAKE_USER"},"password":{"env":"SNOWFLAKE_PASSWORD"},"warehouse":"COMPUTE_WH","database":"DEMO"}}
```

Environment variables are resolved only when executing, never while compiling. Secret fields require environment references; do not put secret values in JSON or commit credentials. The examples are placeholders, not configured accounts. Install the appropriate optional driver extra in the existing project environment (`pip install '.[databricks]'`, `'.[snowflake]'`, or `'.[clickhouse]'`), retaining the repository's documented vendored dependency setup. Building offline does not require these connectors.

Databricks and Snowflake accept `catalog.schema.table` / `database.schema.table`; ClickHouse uses `database.table` and rejects a catalog. Configured identifier spelling and case are preserved: match existing Snowflake quoted identifiers exactly. UTC sessions and ClickHouse nullable joins are configured explicitly.

## Execution boundaries

HamiltonFlow runs **read queries only**. It does not create, load or modify tables. Precreate the source relations and grant the execution identity read access. Remote bound parameters fail closed; use requests that compile to resolved SQL.

## Materialization is out of scope

Earlier revisions described a SQLMesh "vault" materialization path (including a run ledger with no rollback or concurrency protection). That is not supported or documented as a feature. `hamilton_flow.warehouse.sqlmesh_config` remains as an unsupported experimental helper; do not use it for production writes. A real materialization story belongs in a separate package with transactional semantics and live tests.

## References

- [Databricks SQL Connector](https://docs.databricks.com/aws/en/dev-tools/python-sql-connector)
- [Snowflake Python Connector](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-example)
- [ClickHouse Python client](https://clickhouse.com/docs/integrations/language-clients/python/index)
- SQLMesh engine configuration: [Databricks](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/databricks/), [Snowflake](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/snowflake/), [ClickHouse](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/clickhouse/).

## HamiltonFlow usage

```sh
hamilton-flow explain --manifest path/to/semantic_manifest.json --request path/to/request.json --warehouse examples/warehouses/databricks.json
```

`explain` compiles offline. Replace `explain` with `query` only when ready to connect. Python callers can pass `WarehouseClient(config)` to `HamiltonFlow`; close the client after use. The query API returns results only; it does not materialize tables.

## Validation record

* DuckDB: integration and property-based tests on every CI run.
* ClickHouse 26.x: `tests/live/test_clickhouse_live.py` runs six representative requests (simple, ratio, derived, cumulative, filtered, time-grouped) on a real server and requires results identical to DuckDB. CI uses a ClickHouse service container.
* Snowflake, Databricks: offline renderer and connector-stub tests only.
