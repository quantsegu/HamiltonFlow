from __future__ import annotations

from datetime import datetime
from typing import Any

from metricflow.engine.metricflow_engine import MetricFlowQueryRequest


def request_from_dict(options: dict[str, Any]) -> MetricFlowQueryRequest:
    """Convert name-based JSON query options, including ISO time constraints."""
    values = dict(options)
    for name in ("time_constraint_start", "time_constraint_end"):
        if name in values and values[name] is not None:
            values[name] = datetime.fromisoformat(values[name].replace("Z", "+00:00"))
    return MetricFlowQueryRequest.create(**values)
