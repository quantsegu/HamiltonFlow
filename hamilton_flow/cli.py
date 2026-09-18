from __future__ import annotations

import argparse
import json
import logging
from contextlib import ExitStack
from pathlib import Path

import duckdb

from .api import HamiltonFlow
from .client import DuckDBClient
from .requests import request_from_dict
from .warehouse_client import WarehouseClient

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Native MetricFlow semantics with Hamilton execution"
    )
    parser.add_argument("command", choices=["query", "explain", "catalog"])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--database", default=":memory:")
    parser.add_argument("--warehouse", help="JSON warehouse connection configuration")
    parser.add_argument(
        "--request", help="JSON arguments for native MetricFlowQueryRequest.create"
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    with ExitStack() as stack:
        if args.warehouse:
            client = WarehouseClient(json.loads(Path(args.warehouse).read_text()))
            stack.callback(client.close)
        else:
            connection = stack.enter_context(
                duckdb.connect(args.database, read_only=args.database != ":memory:")
            )
            client = DuckDBClient(connection)
        session = HamiltonFlow(args.manifest, client)
        if args.command == "catalog":
            result = session.catalog()
        else:
            request = request_from_dict(json.loads(Path(args.request).read_text()))
            if args.command == "explain":
                statement = session.explain(request).sql_statement
                result = {
                    "sql": client.prepare(statement.sql, statement.bind_parameter_set)
                    if args.warehouse
                    else statement.sql
                }
            else:
                table = session.query(request)
                result = {
                    "columns": [c.column_name for c in table.column_descriptions],
                    "rows": table.rows,
                }
        rendered = json.dumps(result, indent=2, default=str)
        if args.output:
            Path(args.output).write_text(rendered + "\n")
        print(rendered)


if __name__ == "__main__":
    main()
