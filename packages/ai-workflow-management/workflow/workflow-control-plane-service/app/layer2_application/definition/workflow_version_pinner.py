"""Pin ``spec_version`` on every WorkflowNode that doesn't already have one.

Called at save time (create + update) so the persisted workflow locks in
a specific contract version of each referenced worker / business node.
Without this, the engine resolves unversioned references to "latest at
dispatch time" — which means a worker upgrade can silently change the
semantics of a workflow saved months earlier.

Resolution rule (closes the loop from Phase 0 Task 0.4):

  * node.spec_version already set         → keep as-is (author pinned)
  * node.node_definition_id set            → use the leaf definition's spec_version
  * node.worker_type set + not ``builtin:`` → max spec_version across registered workers
  * otherwise (builtin, empty)             → "1.0.0"
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from app.layer1_domain.entities.workflow import Workflow


logger = logging.getLogger(__name__)


class _WorkerRegistryClientLike(Protocol):
    def list_workers(self) -> list[dict[str, Any]]: ...


class _NodeDefinitionRepoLike(Protocol):
    def get_by_id(self, id: str) -> Any: ...


class _ResolverLike(Protocol):
    def resolve_worker_spec_version(
        self,
        worker_type: str,
        pinned: Optional[str],
        workers: list[dict[str, Any]],
    ) -> str: ...


def pin_spec_versions(
    workflow: Workflow,
    *,
    resolver: _ResolverLike,
    worker_registry_client: Optional[_WorkerRegistryClientLike] = None,
    node_definition_repo: Optional[_NodeDefinitionRepoLike] = None,
) -> None:
    """Fill in ``spec_version`` on every node that leaves it ``None``.

    Mutates ``workflow.nodes`` in place. Silent on the registry being
    unavailable (returns "1.0.0" fallbacks) so the save path never fails
    because of missing infrastructure.
    """
    workers: list[dict[str, Any]] = []
    if worker_registry_client is not None:
        try:
            workers = worker_registry_client.list_workers() or []
        except Exception as exc:
            logger.warning("spec pinning: list_workers failed — using empty list: %s", exc)

    for node in workflow.nodes:
        if getattr(node, "spec_version", None):
            continue  # author already pinned
        node.spec_version = _resolve_for_node(
            node, workers, resolver, node_definition_repo,
        )


def _resolve_for_node(
    node: Any,
    workers: list[dict[str, Any]],
    resolver: _ResolverLike,
    node_definition_repo: Optional[_NodeDefinitionRepoLike],
) -> str:
    # Business-node wrapper: leaf NodeDefinition's spec_version is the pin.
    def_id = getattr(node, "node_definition_id", None)
    if def_id and node_definition_repo is not None:
        try:
            nd = node_definition_repo.get_by_id(def_id)
            if nd is not None:
                return getattr(nd, "spec_version", "1.0.0") or "1.0.0"
        except Exception as exc:
            logger.warning("spec pinning: repo.get_by_id(%r) failed: %s", def_id, exc)

    # Worker reference (TASK nodes): pick the highest registered version.
    worker_type = getattr(node, "worker_type", None) or ""
    if worker_type and not worker_type.startswith("builtin:"):
        return resolver.resolve_worker_spec_version(worker_type, None, workers)

    # Builtins and unresolvable references fall back to 1.0.0.
    return "1.0.0"
