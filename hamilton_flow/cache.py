"""Compiled-SQL cache keyed on (manifest hash, request hash, dialect).

Hamilton's built-in cache cannot skip upstream nodes whose outputs (the
MetricFlow engine) are not picklable, so HamiltonFlow looks the key up first and
passes a hit to the DAG as an override; parse/validate/plan/render then do not run.
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path


class CompiledSqlCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, object] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(manifest_fingerprint: str, request_fingerprint: str, dialect: str) -> str:
        return hashlib.sha256(
            f"{manifest_fingerprint}:{request_fingerprint}:{dialect}".encode()
        ).hexdigest()

    def get(self, key: str):
        if key in self._memory:
            self.hits += 1
            return self._memory[key]
        if self.path is not None:
            file = self.path / f"{key}.pkl"
            if file.exists():
                try:
                    value = pickle.loads(file.read_bytes())
                except Exception:  # noqa: BLE001 - corrupt entry: treat as a miss
                    file.unlink(missing_ok=True)
                else:
                    self._memory[key] = value
                    self.hits += 1
                    return value
        self.misses += 1
        return None

    def put(self, key: str, value: object) -> None:
        self._memory[key] = value
        if self.path is not None:
            tmp = self.path / f"{key}.tmp"
            tmp.write_bytes(pickle.dumps(value))
            tmp.replace(self.path / f"{key}.pkl")
