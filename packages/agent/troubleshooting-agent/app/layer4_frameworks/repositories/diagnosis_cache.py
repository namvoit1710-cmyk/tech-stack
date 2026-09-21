"""Diagnosis cache — the Software 5.0 lever: the first diagnosis of a given error signature
is remembered and replayed for everyone after, so the same issue always yields the same
verdict (A==B) and the LLM is called less over time.

Keyed by `error_signature(run_error)` (see layer1 failure_classifier). Backed by a JSON file
when a path is given; in-memory otherwise. ponytail: flat JSON dict, no eviction — fine for the
bounded set of distinct workflow errors; add an LRU/TTL only if it grows unbounded."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.layer2_application.interfaces.diagnosis_cache_interface import IDiagnosisCache

logger = logging.getLogger(__name__)


class DiagnosisCache(IDiagnosisCache):
    def __init__(self, path: str = ""):
        self._path = path
        self._store: dict[str, dict[str, Any]] = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self._store = json.load(fh)
            except Exception as exc:  # a corrupt cache must never break diagnosis
                logger.warning("Could not load diagnosis cache %s: %s", path, exc)

    def get(self, signature: str) -> dict[str, Any] | None:
        return self._store.get(signature)

    def put(self, signature: str, verdict: dict[str, Any]) -> None:
        self._store[signature] = verdict
        if not self._path:
            return
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._store, fh, ensure_ascii=False, indent=2)
        except Exception as exc:  # persistence is best-effort; keep serving from memory
            logger.warning("Could not persist diagnosis cache %s: %s", self._path, exc)
