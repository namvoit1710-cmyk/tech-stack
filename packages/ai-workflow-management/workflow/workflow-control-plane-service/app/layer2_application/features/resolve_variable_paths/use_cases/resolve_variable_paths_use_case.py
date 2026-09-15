"""Batched variable-path evaluator for the workflow builder.

Serves ``POST /runs/{run_id}/resolve`` with body
``{paths: ["$.node.x", "$artifacts.y.file_id", ...], task_id?}``.

Thin wrapper around ``VariableResolver`` with these properties:

- **Per-path isolation**: one path failing does not fail the batch. The result
  list carries ``{status: "ok" | "error"}`` per path.
- **Read-only**: never mutates ``run.context``.
- **Batched for the builder**: FE can evaluate a whole mapping (multiple paths
  for one node) in one round-trip, avoiding N+1 to ``/data``.
- **Kind classification**: each ``"ok"`` result carries a ``kind`` hint
  (``scalar`` | ``file_ref`` | ``artifact`` | ``artifact_row`` |
  ``artifact_column``) so FE can render the preview appropriately.

Limits: 100 paths per call; too many → ``BAD_REQUEST``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer2_application.resolve.variable_resolver import (
    VariableResolutionError,
    VariableResolver,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.run_repository_interface import IRunRepository

MAX_PATHS = 100


@dataclass
class ResolveVariablePathsCommand:
    run_id: str = ""
    paths: list[str] = field(default_factory=list)
    task_id: Optional[str] = None


@dataclass
class PathResult:
    path: str
    status: str              # "ok" | "error"
    kind: Optional[str] = None   # scalar | file_ref | artifact | artifact_row | artifact_column
    value: Any = None
    error: Optional[dict[str, Any]] = None


@dataclass
class ResolveVariablePathsResult:
    results: list[PathResult] = field(default_factory=list)
    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ResolveVariablePathsUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        variable_resolver: VariableResolver,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self._resolver = variable_resolver
        # Phase 3b.1 stopped writing node outputs to ``run.context.variables``;
        # the projector view is the authoritative read side for per-node
        # outputs (keyed by node id). Without this view store wired, paths
        # like ``$<node_id>.__file_id`` raise PATH_NOT_FOUND even after the
        # node has completed. Mirrors ``GetVariableSuggestionsUseCase``.
        self._view_store = run_context_view_store

    def _resolve_variables(self, run: Any) -> dict[str, Any]:
        """View-on-aggregate merge: projector wins for per-node keys; the
        aggregate fills gaps (notably ``"artifacts"``, mutated directly by
        ``register_artifacts`` and not covered by ``NodeOutputProduced``).
        """
        aggregate = getattr(getattr(run, "context", None), "variables", {}) or {}
        if self._view_store is None:
            return dict(aggregate)
        try:
            view = self._view_store.get(run.id)
        except Exception:
            view = None
        if view is None:
            return dict(aggregate)
        merged = dict(aggregate)
        for k, v in view.variables.items():
            merged[k] = v
        return merged

    async def execute(
        self, command: ResolveVariablePathsCommand,
    ) -> ResolveVariablePathsResult:
        if len(command.paths) > MAX_PATHS:
            return ResolveVariablePathsResult(
                error=f"too many paths; limit is {MAX_PATHS}",
                error_code=ErrorCode.BAD_REQUEST.code,
                error_details={"limit": MAX_PATHS, "received": len(command.paths)},
            )

        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return ResolveVariablePathsResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        context = self._resolve_variables(run)

        results: list[PathResult] = []
        for path in command.paths:
            results.append(await self._resolve_one(path, context))

        return ResolveVariablePathsResult(results=results)

    async def _resolve_one(self, path: str, context: dict[str, Any]) -> PathResult:
        if not isinstance(path, str) or not path.strip():
            return PathResult(
                path=str(path), status="error",
                error={"code": "INVALID_PATH", "message": "path must be a non-empty string"},
            )
        try:
            value = await self._resolver.resolve(path, context)
        except VariableResolutionError as exc:
            return PathResult(
                path=path, status="error",
                error={"code": "PATH_NOT_FOUND", "message": str(exc)},
            )
        except Exception as exc:  # pragma: no cover — safety net
            return PathResult(
                path=path, status="error",
                error={"code": "INTERNAL_ERROR", "message": str(exc)},
            )

        # Classify the kind for the FE preview UX.
        kind = _classify(value, raw_path=path)
        return PathResult(path=path, status="ok", kind=kind, value=value)


# ---------------------------------------------------------------------------
# Kind classification
# ---------------------------------------------------------------------------

_ARTIFACT_ROW_MARKER = "[*]"


def _classify(value: Any, raw_path: str) -> str:
    """Heuristic mapping from resolved value → FE-facing ``kind``.

    The resolver returns whatever the expression points at. For the builder
    preview:

    - A ``__file_ref`` dict → ``"artifact"`` (the whole table).
    - A dict with ``file_id`` key (file metadata) → ``"file_ref"``.
    - A list of dicts from a ``[*]`` iterator path → ``"artifact_row"``.
    - A list of primitives from a ``[*].col`` path → ``"artifact_column"``.
    - Everything else → ``"scalar"``.
    """
    if is_file_ref(value):
        return "artifact"
    if isinstance(value, dict) and value.get("file_id") and not value.get("__file_ref"):
        return "file_ref"
    if isinstance(value, list) and _ARTIFACT_ROW_MARKER in raw_path:
        if value and all(isinstance(r, dict) for r in value):
            return "artifact_row"
        return "artifact_column"
    return "scalar"
