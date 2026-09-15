"""Read-only view over a workflow run's state — adapter-layer stability shim.

Phase 4a of the event-driven migration. Read-only query-side features
(GetRunStatus, GetRunContext, GetNodeOutput, ...) take an
``IRunContextView`` instead of the full ``WorkflowRun`` aggregate.

Why this indirection exists:
    The engine is migrating to an event-driven projector model. Today the
    read side loads a ``WorkflowRun`` from ``IRunRepository`` and reaches
    into ``run.context.variables`` directly. When Phase 4b lands the
    Projector, the authoritative read-side will be a projection over an
    event log — NOT a ``WorkflowRun`` dataclass. By consuming an
    ``IRunContextView`` today, feature code stays stable across that swap.

    The adapter layer (REST controllers, DTOs, GraphQL) sees zero change.
    This is the core "adapter-layer stability" principle from
    ``docs/design/adapter-layer-stability-plan.md``.

Relationship to ``RunContextSnapshot``:
    ``RunContextSnapshot`` (``layer1_domain/entities/run_context_snapshot.py``)
    is the default concrete implementation. Structural typing via Protocol
    means any object exposing the same shape — including a future
    projector-backed implementation — satisfies ``IRunContextView``.
"""

from __future__ import annotations

from typing import Any, Protocol
from collections.abc import Mapping

from app.layer1_domain.entities.workflow_run import GraphTopology


class IRunContextView(Protocol):
    """Read-only facade over a workflow run's state.

    All properties are read-only; implementations must not expose mutation
    entry points. Callers treat the view as an immutable snapshot.
    """

    @property
    def run_id(self) -> str: ...

    @property
    def workflow_id(self) -> str: ...

    @property
    def workflow_version(self) -> str: ...

    @property
    def status(self) -> str:
        """``RunStatus.value`` as a plain string (already unwrapped)."""

    @property
    def started_at(self) -> str: ...

    @property
    def completed_at(self) -> str | None: ...

    @property
    def error(self) -> str | None: ...

    @property
    def output_data(self) -> dict[str, Any] | None: ...

    @property
    def input_data(self) -> dict[str, Any]: ...

    @property
    def variables(self) -> Mapping[str, Any]:
        """Node outputs keyed by node id (and the fixed ``"input"`` key)."""

    @property
    def last_output_ports(self) -> Mapping[str, str]:
        """Per-node last ``output_port`` from ``NodeOutputProduced``."""

    @property
    def last_taken_edge_ports(self) -> Mapping[str, Mapping[str, str]]:
        """Per-node ``{edge_id: port_id}`` for CONDITION/SWITCH branches."""

    @property
    def completed_nodes(self) -> frozenset[str]: ...

    @property
    def skipped_nodes(self) -> frozenset[str]: ...

    @property
    def entry_nodes(self) -> tuple[str, ...]:
        """Entry nodes of the DAG — convenience wrapper over ``topology``."""

    @property
    def topology(self) -> GraphTopology:
        """Underlying DAG metadata.

        NOTE: returned as the domain dataclass today. Phase 4b may replace
        this with a lighter-weight projection type — consumers should only
        read fields, never mutate.
        """

    @property
    def child_runs(self) -> Mapping[str, str]: ...

    @property
    def parent_run_id(self) -> str | None: ...

    @property
    def root_run_id(self) -> str: ...

    @property
    def depth_level(self) -> int: ...


class IRunContextViewStore(Protocol):
    """Factory protocol for acquiring read-only views of workflow runs.

    Production implementation: ``CachedProjectorRunContextViewStore``
    reads from the event log with a workflow_repo / run_repo fallback
    for ``topology`` / ``get_by_root_id``. Phase 3b.3 removed the
    prior ``LiveRunContextViewStore`` + ``ParityComparatorViewStore``;
    tests that need aggregate-reading semantics use
    ``tests/helpers/view_store_doubles.LiveViewStoreDouble``.
    """

    def get(self, run_id: str) -> IRunContextView | None:
        """Return a view for ``run_id`` or ``None`` if the run does not exist."""

    def get_by_root_id(self, root_id: str) -> list[IRunContextView]:
        """Return views of every run (including nested) rooted at ``root_id``."""
