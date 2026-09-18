from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from metricflow.data_table.mf_table import MetricFlowDataTable
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

logger = logging.getLogger(__name__)


def semantic_manifest(manifest_path: str) -> PydanticSemanticManifest:
    """Load the native MetricFlow YAML directory or serialized semantic manifest."""
    path = Path(manifest_path)
    if path.is_dir():
        manifest = parse_directory_of_yaml_files_to_semantic_manifest(
            str(path)
        ).semantic_manifest
    else:
        manifest = PydanticSemanticManifest.parse_raw(path.read_text())
    SemanticManifestValidator[PydanticSemanticManifest]().checked_validations(manifest)
    return manifest


def metric_engine(
    semantic_manifest: PydanticSemanticManifest, sql_client: Any
) -> MetricFlowEngine:
    """Build the unchanged upstream query planner and optimizer."""
    return MetricFlowEngine(SemanticManifestLookup(semantic_manifest), sql_client)


def compiled_query(
    metric_engine: MetricFlowEngine, query_request: MetricFlowQueryRequest
) -> MetricFlowExplainResult:
    """Expose upstream SQL and dataflow plans as a Hamilton node."""
    return metric_engine.explain(query_request)


def query_result(
    compiled_query: MetricFlowExplainResult, sql_client: Any
) -> MetricFlowDataTable:
    """Execute the compiled statement through the supplied backend client."""
    statement = compiled_query.sql_statement
    return sql_client.query(statement.sql, statement.bind_parameter_set)
