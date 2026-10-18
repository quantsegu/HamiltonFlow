"""The Hamilton dataflow: ingest -> parse -> validate -> plan -> render -> execute -> check.

Stage tags (``@tag(stage=...)``) are the lineage vocabulary used by the UI,
OpenLineage emitter and ``HamiltonFlow.lineage()``. ``compiled_sql`` is the
cache boundary (see ``cache.py``): a hit is injected as an override, so parsing,
validation, planning and rendering do not run.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hamilton.function_modifiers import check_output_custom, tag
from hamilton.data_quality.base import DataValidator, ValidationResult
from metricflow.engine.metricflow_engine import (
    MetricFlowEngine,
    MetricFlowExplainResult,
    MetricFlowQueryRequest,
)
from metricflow_semantic_interfaces.implementations.semantic_manifest import (
    PydanticSemanticManifest,
)
from metricflow_semantic_interfaces.parsing.dir_to_model import (
    parse_directory_of_yaml_files_to_semantic_manifest,
)
from metricflow_semantic_interfaces.validations.semantic_manifest_validator import (
    SemanticManifestValidator,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet

from .errors import ManifestError, WarehouseError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompiledSql:
    sql: str
    bind_parameters: SqlBindParameterSet
    dialect: str


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    sql: str

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]


# --- ingest ---------------------------------------------------------------


def _manifest_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix in {".yaml", ".yml"})
    return [path]


@tag(stage="ingest", kind="hash")
def manifest_fingerprint(manifest_path: str) -> str:
    """Content hash of the manifest sources; the cache key for everything compiled."""
    path = Path(manifest_path)
    if not path.exists():
        raise ManifestError(f"Manifest not found: {manifest_path}")
    digest = hashlib.sha256()
    for file in _manifest_files(path):
        digest.update(file.relative_to(path if path.is_dir() else file.parent).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


@tag(stage="ingest", kind="hash")
def request_fingerprint(query_request: MetricFlowQueryRequest) -> str:
    """Stable hash of the request (the generated request_id is excluded)."""
    fields = {
        k: v
        for k, v in vars(query_request).items()
        if k != "request_id" and v is not None
    }
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, default=str).encode()
    ).hexdigest()


# --- parse / validate -----------------------------------------------------


def load_manifest(manifest_path: str) -> PydanticSemanticManifest:
    """Parse and validate in one call (non-DAG convenience)."""
    return semantic_manifest(parsed_manifest(manifest_path))


@tag(stage="parse")
def parsed_manifest(manifest_path: str) -> PydanticSemanticManifest:
    """Parse native MetricFlow YAML or a serialized semantic_manifest.json."""
    path = Path(manifest_path)
    try:
        if path.is_dir():
            result = parse_directory_of_yaml_files_to_semantic_manifest(str(path))
            return result.semantic_manifest
        return PydanticSemanticManifest.parse_raw(path.read_text())
    except Exception as exc:
        raise ManifestError(f"Cannot parse manifest {manifest_path}: {exc}") from exc


@tag(stage="validate")
def semantic_manifest(parsed_manifest: PydanticSemanticManifest) -> PydanticSemanticManifest:
    """Run upstream manifest validation; raise ManifestError with all issues."""
    try:
        SemanticManifestValidator[PydanticSemanticManifest]().checked_validations(
            parsed_manifest
        )
    except Exception as exc:
        raise ManifestError(f"Manifest failed validation: {exc}") from exc
    return parsed_manifest


# --- plan / render --------------------------------------------------------


@tag(stage="plan")
def metric_engine(
    semantic_manifest: PydanticSemanticManifest, sql_client: Any
) -> MetricFlowEngine:
    """The unchanged upstream query planner and optimizer."""
    return MetricFlowEngine(SemanticManifestLookup(semantic_manifest), sql_client)


@tag(stage="render")
def compiled_query(
    metric_engine: MetricFlowEngine, query_request: MetricFlowQueryRequest
) -> MetricFlowExplainResult:
    """Upstream explain: resolve dimensions, build dataflow plan, render SQL."""
    return metric_engine.explain(query_request)


@tag(stage="render", cache_boundary="true")
def compiled_sql(
    compiled_query: MetricFlowExplainResult,
    manifest_fingerprint: str,
    request_fingerprint: str,
    sql_dialect: str,
) -> CompiledSql:
    """Cacheable SQL artifact keyed on (manifest hash, request hash, dialect)."""
    statement = compiled_query.sql_statement
    return CompiledSql(statement.sql, statement.bind_parameter_set, sql_dialect)


# --- execute / post-process ----------------------------------------------


class ResultShapeValidator(DataValidator):
    """Fail if the result has duplicate or missing columns."""

    def applies_to(self, datatype: type[type]) -> bool:
        return datatype is QueryResult

    def description(self) -> str:
        return "Result has named, unique columns"

    @classmethod
    def name(cls) -> str:
        return "result_shape"

    def validate(self, data: Any) -> ValidationResult:
        columns = list(data.columns)
        ok = bool(columns) and len(columns) == len(set(columns))
        return ValidationResult(
            passes=ok,
            message="ok" if ok else f"empty or duplicate columns: {columns}",
            diagnostics={"columns": columns},
        )


@tag(stage="execute")
def raw_result(compiled_sql: CompiledSql, sql_client: Any) -> QueryResult:
    """Run the compiled SQL on the caller's client."""
    try:
        table = sql_client.query(compiled_sql.sql, compiled_sql.bind_parameters)
    except Exception as exc:
        raise WarehouseError(
            f"Warehouse rejected the compiled query: {exc}",
            details={"sql": compiled_sql.sql},
        ) from exc
    return QueryResult(
        columns=tuple(c.column_name for c in table.column_descriptions),
        rows=tuple(tuple(r) for r in table.rows),
        sql=compiled_sql.sql,
    )


@tag(stage="check")
@check_output_custom(ResultShapeValidator(importance="fail"))
def query_result(raw_result: QueryResult) -> QueryResult:
    """Data-quality-checked result returned to callers."""
    return raw_result
