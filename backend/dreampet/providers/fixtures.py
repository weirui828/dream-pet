"""record / replay fixtures: request/response pairs keyed by a hash of the request."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any


def request_key(role: str, op: str, provider: str, model: str | None, payload: dict[str, Any]) -> str:
    blob = json.dumps({"role": role, "op": op, "provider": provider, "model": model, "req": payload},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class FixtureStore:
    def __init__(self, root: Path):
        self.root = root
        self.used: set[str] = set()
        self._lock = threading.Lock()

    def _path(self, role: str, key: str) -> Path:
        return self.root / role / key[:2] / f"{key}.json"

    def load(self, role: str, key: str) -> Any | None:
        p = self._path(role, key)
        if not p.exists():
            return None
        with self._lock:
            self.used.add(str(p.relative_to(self.root)))
        return json.loads(p.read_text())["response"]

    def save(self, role: str, key: str, op: str, request: dict[str, Any], response: Any) -> None:
        p = self._path(role, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"role": role, "op": op, "request": request, "response": response},
                                ensure_ascii=False, indent=1, default=str))
        with self._lock:
            self.used.add(str(p.relative_to(self.root)))

    def all_files(self) -> list[Path]:
        return sorted(self.root.glob("*/*/*.json")) if self.root.exists() else []

    def prune(self, keep: set[str]) -> list[Path]:
        removed = []
        for p in self.all_files():
            if str(p.relative_to(self.root)) not in keep:
                p.unlink()
                removed.append(p)
        return removed
