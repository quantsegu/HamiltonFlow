from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ManifestError


@dataclass
class ConversionReport:
    """What was converted and, explicitly, what was not and why."""

    converted: list[str] = field(default_factory=list)
    unsupported: list[dict[str, str]] = field(default_factory=list)

    def skip(self, kind: str, name: str, reason: str) -> None:
        self.unsupported.append({"kind": kind, "name": name, "reason": reason})

    def to_dict(self) -> dict[str, Any]:
        return {"converted": self.converted, "unsupported": self.unsupported}


@dataclass
class Conversion:
    """Native MetricFlow YAML documents plus the conversion report."""

    documents: list[dict[str, Any]]
    report: ConversionReport

    def to_yaml(self) -> str:
        return yaml.safe_dump_all(self.documents, sort_keys=False)

    def write(self, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "converted.yaml").write_text(self.to_yaml())
        return directory

    def validate(self) -> None:
        """Raise ManifestError unless upstream MetricFlow accepts the result."""
        from ..nodes import load_manifest

        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp)
            load_manifest(tmp)

    def manifest(self):
        from ..nodes import load_manifest

        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp)
            return load_manifest(tmp)


def node_relation(source: str) -> dict[str, str] | None:
    """Split ``[database.]schema.table``; None if ``source`` is not a plain table."""
    parts = source.strip().split(".")
    if not 1 <= len(parts) <= 3 or any(
        not p or not all(c.isalnum() or c == "_" for c in p) for p in parts
    ):
        return None
    relation = {"schema_name": parts[-2] if len(parts) > 1 else "main", "alias": parts[-1]}
    if len(parts) == 3:
        relation["database"] = parts[0]
    return relation


def require_yaml(text: str, what: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Cannot parse {what}: {exc}") from exc


def project_configuration(time_spine: str = "main.time_spine", column: str = "date_day") -> dict[str, Any]:
    """MetricFlow requires one time spine table; callers must provide it physically."""
    relation = node_relation(time_spine)
    return {
        "project_configuration": {
            "time_spines": [
                {"node_relation": relation, "primary_column": {"name": column, "time_granularity": "day"}}
            ]
        }
    }
