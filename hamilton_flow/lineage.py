"""Dependency-free OpenLineage run events for HamiltonFlow executions.

Events follow the OpenLineage RunEvent shape (START / COMPLETE / FAIL). Input
datasets are the physical tables the compiled SQL reads, extracted with SQLGlot.
Pass any ``Callable[[dict], None]`` as a sink; ``http_sink`` and ``file_sink`` are
provided.
"""

from __future__ import annotations

import json
import time
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlglot
from hamilton.lifecycle import api as lc

PRODUCER = "https://github.com/quantsegu/HamiltonFlow"
SCHEMA = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
Sink = Callable[[dict], None]


def file_sink(path: str | Path) -> Sink:
    def write(event: dict) -> None:
        with open(path, "a") as handle:
            handle.write(json.dumps(event, default=str) + "\n")

    return write


def http_sink(url: str, timeout: float = 5.0) -> Sink:
    def post(event: dict) -> None:
        request = urllib.request.Request(
            url,
            data=json.dumps(event, default=str).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=timeout).read()

    return post


def tables_in_sql(sql: str, dialect: str | None = None) -> list[str]:
    """Fully-qualified physical tables read by ``sql`` (CTE names excluded)."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except (sqlglot.errors.SqlglotError, ValueError):
        return []
    ctes = {c.alias_or_name for c in tree.find_all(sqlglot.exp.CTE)}
    names = {
        ".".join(p for p in (t.catalog, t.db, t.name) if p)
        for t in tree.find_all(sqlglot.exp.Table)
        if t.name and t.name not in ctes
    }
    return sorted(names)


class OpenLineageEmitter(lc.GraphExecutionHook):
    """Hamilton lifecycle adapter: one OpenLineage run per graph execution."""

    def __init__(
        self,
        sink: Sink,
        namespace: str = "hamilton_flow",
        job_name: str = "hamilton_flow.query",
        sql_dialect: str | None = None,
    ) -> None:
        self.sink = sink
        self.namespace = namespace
        self.job_name = job_name
        self.sql_dialect = sql_dialect
        self._starts: dict[str, dict[str, Any]] = {}

    def _event(self, kind: str, run_id: str, facets: dict, **extra: Any) -> dict:
        return {
            "eventType": kind,
            "eventTime": datetime.now(UTC).isoformat(),
            "producer": PRODUCER,
            "schemaURL": SCHEMA,
            "run": {"runId": str(uuid.uuid5(uuid.NAMESPACE_URL, run_id)), "facets": facets},
            "job": {"namespace": self.namespace, "name": self.job_name},
            "inputs": [],
            "outputs": [],
            **extra,
        }

    def run_before_graph_execution(self, *, run_id, inputs, final_vars, **_):
        self._starts[run_id] = {"final_vars": list(final_vars)}
        facets = {
            "hamilton_flow": {
                "_producer": PRODUCER,
                "_schemaURL": PRODUCER,
                "final_vars": list(final_vars),
            }
        }
        self.sink(self._event("START", run_id, facets))

    def run_after_graph_execution(self, *, run_id, success, error, results, **_):
        facets: dict[str, Any] = {}
        inputs: list[dict] = []
        sql = None
        if results:
            for value in results.values():
                sql = getattr(value, "sql", None)
                if isinstance(sql, str):
                    break
        if isinstance(sql, str):
            inputs = [
                {"namespace": self.namespace, "name": t}
                for t in tables_in_sql(sql, self.sql_dialect)
            ]
            facets["sql"] = {"_producer": PRODUCER, "_schemaURL": PRODUCER, "query": sql}
        if error is not None:
            facets["errorMessage"] = {
                "_producer": PRODUCER,
                "_schemaURL": PRODUCER,
                "message": str(error),
                "programmingLanguage": "python",
            }
        self._starts.pop(run_id, None)
        self.sink(
            self._event("COMPLETE" if success else "FAIL", run_id, facets, inputs=inputs)
        )


class RunRecorder(lc.NodeExecutionHook):
    """Records per-node wall time for the most recent HamiltonFlow call."""

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self._started: dict[tuple[str, str], float] = {}

    def reset(self) -> None:
        self.nodes = []
        self._started = {}

    def run_before_node_execution(self, *, node_name, run_id, **_):
        self._started[(run_id, node_name)] = time.perf_counter()

    def run_after_node_execution(self, *, node_name, node_tags, run_id, success, error, **_):
        began = self._started.pop((run_id, node_name), time.perf_counter())
        self.nodes.append(
            {
                "node": node_name,
                "stage": node_tags.get("stage"),
                "ms": round((time.perf_counter() - began) * 1000, 3),
                "ok": success,
            }
        )
