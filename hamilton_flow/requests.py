from __future__ import annotations

from datetime import datetime
from typing import Any

from metricflow.engine.metricflow_engine import MetricFlowQueryRequest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import RequestError


class QueryRequest(BaseModel):
    """Validated, name-based query request. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    metric_names: list[str] | None = None
    group_by_names: list[str] | None = None
    where_constraints: list[str] | None = None
    order_by_names: list[str] | None = None
    limit: int | None = Field(default=None, ge=0)
    saved_query_name: str | None = None
    time_constraint_start: datetime | None = None
    time_constraint_end: datetime | None = None

    @field_validator("time_constraint_start", "time_constraint_end", mode="before")
    @classmethod
    def _iso(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"not an ISO-8601 timestamp: {value!r}") from exc
        return value

    def to_native(self) -> MetricFlowQueryRequest:
        if self.saved_query_name is None and not self.metric_names:
            raise RequestError(
                "Provide metric_names or saved_query_name.",
                details={"field": "metric_names"},
            )
        if (
            self.time_constraint_start
            and self.time_constraint_end
            and self.time_constraint_start > self.time_constraint_end
        ):
            raise RequestError(
                "time_constraint_start is after time_constraint_end.",
                details={"field": "time_constraint_start"},
            )
        return MetricFlowQueryRequest.create(**self.model_dump(exclude_none=True))


def request_from_dict(options: dict[str, Any]) -> MetricFlowQueryRequest:
    """Convert name-based JSON query options; raise RequestError on bad input."""
    if not isinstance(options, dict):
        raise RequestError("Request must be a JSON object.")
    try:
        return QueryRequest.model_validate(options).to_native()
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors()
        ]
        raise RequestError(
            "Invalid query request: "
            + "; ".join(f"{e['field']}: {e['message']}" for e in errors),
            details={"errors": errors},
        ) from exc
