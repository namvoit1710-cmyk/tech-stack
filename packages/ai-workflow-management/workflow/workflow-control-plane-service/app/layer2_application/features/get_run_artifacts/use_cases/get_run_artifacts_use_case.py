"""Return the run-level ``$artifacts`` registry.

Serves ``GET /runs/{run_id}/artifacts``. The registry is populated by
``register_artifacts`` at node completion (see Unit A) and keyed by the
``artifact_key`` name the producing node declared. Each entry carries file
metadata + provenance (``produced_by_node``, ``produced_by_task``,
``registered_at``) so consumers can link entries back to the node that
produced them.

Registry shape is defined in
``app.layer1_domain.entities.data_reference.register_artifacts``; this use case
is a thin read over ``run.context.variables["artifacts"]``.

An ETag is computed over the registry content so FE can cache via
``If-None-Match`` just like the per-node endpoints. Empty registry → stable
"empty" ETag (different from "registry with zero real entries" — both
stabilise to the same hash since the payload is the same).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import variables_of
from app.layer2_application.repositories.run_repository_interface import IRunRepository


@dataclass
class GetRunArtifactsCommand:
    run_id: str = ""


@dataclass
class GetRunArtifactsResult:
    artifacts: dict[str, Any] = field(default_factory=dict)
    etag: str = "sha256:empty"

    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetRunArtifactsUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        run_context_view_store: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self._view_store = run_context_view_store

    async def execute(self, command: GetRunArtifactsCommand) -> GetRunArtifactsResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return GetRunArtifactsResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        registry = self._registry(run, command.run_id)
        return GetRunArtifactsResult(
            artifacts=registry,
            etag=self._etag(registry),
        )

    # ------------------------------------------------------------------

    def _registry(self, run: Any, run_id: str) -> dict[str, Any]:
        # ``run.context.variables`` is {} on every run created since DROP
        # CONTEXT, which made this endpoint return an empty registry for every
        # artifact ever produced — silently, since an empty panel and a run with
        # no artifacts look identical.
        variables = variables_of(run_id, self._view_store, run)
        registry = variables.get("artifacts") or {}
        if not isinstance(registry, dict):
            return {}
        # Only surface entries that look like metadata objects (post-Unit-A
        # shape). A legacy bare-string entry would be invalid for the new
        # contract; filter it out and log.
        filtered: dict[str, Any] = {}
        for name, entry in registry.items():
            if isinstance(entry, dict) and entry.get("file_id"):
                filtered[name] = entry
        return filtered

    @staticmethod
    def _etag(registry: dict[str, Any]) -> str:
        if not registry:
            return "sha256:empty"
        payload = json.dumps(registry, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
