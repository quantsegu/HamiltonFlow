"""Structured error types separating who can fix a failure."""

from __future__ import annotations

from typing import Any


class HamiltonFlowError(Exception):
    """Base class. ``kind`` names the layer that failed: manifest, request, warehouse."""

    kind = "internal"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "kind": self.kind,
                "type": type(self).__name__,
                "message": self.message,
                "details": self.details,
            }
        }


class ManifestError(HamiltonFlowError):
    """The semantic manifest is missing, unparsable or fails validation."""

    kind = "manifest"


class RequestError(HamiltonFlowError):
    """The query request is malformed or refers to unknown metrics/dimensions."""

    kind = "request"


class WarehouseError(HamiltonFlowError):
    """The backend rejected or failed to run the compiled SQL."""

    kind = "warehouse"


class ResultCheckError(HamiltonFlowError):
    """A post-execution data-quality check failed."""

    kind = "result"
