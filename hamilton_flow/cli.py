from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import ExitStack
from pathlib import Path

import duckdb

from .api import HamiltonFlow
from .client import DuckDBClient
from .errors import HamiltonFlowError
from .interchange import dump_ossie, from_lookml, from_ossie, to_ossie
from .nodes import load_manifest
from .requests import request_from_dict
from .warehouse_client import WarehouseClient

logger = logging.getLogger(__name__)
EXIT_CODES = {"request": 2, "manifest": 3, "warehouse": 4, "result": 5}
QUERY_COMMANDS = ["query", "explain", "catalog", "serve"]
INTERCHANGE = ["import-ossie", "export-ossie", "import-lookml"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Native MetricFlow semantics with Hamilton execution"
    )
    parser.add_argument("command", choices=QUERY_COMMANDS + INTERCHANGE)
    parser.add_argument("--manifest", help="MetricFlow YAML dir or manifest JSON")
    parser.add_argument("--project", help="Builder project directory (DuckDB data + model); enables the Data/Metrics UI")
    parser.add_argument("--input", help="Ossie/LookML file or directory (import commands)")
    parser.add_argument("--database", default=":memory:")
    parser.add_argument("--warehouse", help="JSON warehouse connection configuration")
    parser.add_argument("--request", help="JSON arguments for the query request")
    parser.add_argument("--output")
    parser.add_argument("--cache", help="directory for the compiled-SQL cache")
    parser.add_argument("--lineage-file", help="append OpenLineage events (JSONL)")
    parser.add_argument("--transport", choices=["http", "mcp", "mcp-http"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args(argv)


def run_interchange(args) -> dict:
    if args.command == "export-ossie":
        document, report = to_ossie(load_manifest(args.manifest))
        text = dump_ossie(document)
        if args.output:
            Path(args.output).write_text(text)
        else:
            print(text)
        return {"report": report.to_dict()}
    conversion = (from_ossie if args.command == "import-ossie" else from_lookml)(args.input)
    if not args.output:
        raise SystemExit("--output (target directory) is required for imports")
    conversion.write(args.output)
    return {"written": str(args.output), "report": conversion.report.to_dict()}


def prepare_project(args) -> None:
    """Point the session at a project dir with its own writable DuckDB file."""
    from . import builder
    from .nodes import load_manifest

    project = Path(args.project)
    project.mkdir(parents=True, exist_ok=True)
    if args.manifest and not (project / builder.SPEC_FILE).exists():
        builder.save(project, builder.spec_from_manifest(load_manifest(args.manifest)))
    args.manifest = str(project)
    if not args.warehouse and args.database == ":memory:":
        args.database = str(project / "data.duckdb")


def open_session(args, stack: ExitStack):
    if args.warehouse:
        client = WarehouseClient(json.loads(Path(args.warehouse).read_text()))
        stack.callback(client.close)
    else:
        connection = stack.enter_context(
            duckdb.connect(
                args.database, read_only=args.database != ":memory:" and not args.project
            )
        )
        client = DuckDBClient(connection)
    sink = None
    if args.lineage_file:
        from .lineage import file_sink

        sink = file_sink(args.lineage_file)
    return client, HamiltonFlow(
        args.manifest, client, cache=args.cache or False, lineage_sink=sink
    )


def run(args) -> dict | None:
    if args.command in INTERCHANGE:
        return run_interchange(args)
    if args.project:
        prepare_project(args)
    if not args.manifest:
        raise SystemExit("--manifest or --project is required")
    with ExitStack() as stack:
        client, session = open_session(args, stack)
        if args.command == "serve":
            serve(args, session)
            return None
        if args.command == "catalog":
            return session.catalog()
        request = request_from_dict(json.loads(Path(args.request).read_text()))
        if args.command == "explain":
            sql = session.compile(request).sql
            return {"sql": client.prepare(sql) if args.warehouse else sql}
        table = session.query(request)
        return {"columns": list(table.columns), "rows": table.rows}


def serve(args, session) -> None:
    from . import server, sources

    if args.project:
        sources.ensure_time_spine(sources.connection_of(session.sql_client))

    if args.transport == "http":
        import uvicorn

        uvicorn.run(server.create_app(session, project=args.project), host=args.host, port=args.port)
    else:
        mcp = server.create_mcp(session)
        if args.transport == "mcp":
            mcp.run("stdio")
        else:
            mcp.settings.host, mcp.settings.port = args.host, args.port
            mcp.run("streamable-http")


def main(argv=None) -> None:
    args = parse_args(argv)
    try:
        result = run(args)
    except HamiltonFlowError as exc:
        print(json.dumps(exc.to_dict(), indent=2, default=str), file=sys.stderr)
        raise SystemExit(EXIT_CODES.get(exc.kind, 1)) from exc
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": {"kind": "request", "message": f"Invalid JSON: {exc}"}}), file=sys.stderr)
        raise SystemExit(2) from exc
    if result is None:
        return
    rendered = json.dumps(result, indent=2, default=str)
    if args.output and args.command in QUERY_COMMANDS:
        Path(args.output).write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
