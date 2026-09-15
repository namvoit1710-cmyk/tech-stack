"""Immutable read-only snapshot of a ``WorkflowRun``.

Default concrete implementation of ``IRunContextView``
(``layer2_application/interfaces/run_context_view_interface.py``).

Lives in ``layer1_domain`` because it is a pure value object over domain
entities with no framework dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun


@dataclass(frozen=True)
class RunContextSnapshot:
    """Frozen shallow-copy of the state query-side features care about.

    - ``frozen=True`` prevents reassignment of snapshot attributes.
    - Collections (``variables``, ``completed_nodes`` etc.) are *shallow*
      copies taken at construction. Mutating the snapshot's collections
      will NOT affect the source ``WorkflowRun``, and vice-versa.
    - Values inside ``variables`` (file_refs, dicts) are still references
      to the original data — callers are expected to treat them as
      read-only per the ``IRunContextView`` contract.
    """

    run_id: str
    workflow_id: str
    workflow_version: str
    status: str
    started_at: str
    completed_at: str | None
    error: str | None
    output_data: dict[str, Any] | None
    input_data: dict[str, Any]
    variables: dict[str, Any]
    last_output_ports: dict[str, str]
    last_taken_edge_ports: dict[str, dict[str, str]]
    completed_nodes: frozenset[str]
    skipped_nodes: frozenset[str]
    topology: GraphTopology
    child_runs: dict[str, str]
    parent_run_id: str | None
    root_run_id: str
    depth_level: int

    @property
    def entry_nodes(self) -> tuple[str, ...]:
        return tuple(self.topology.entry_nodes)

    @classmethod
    def from_run(cls, run: WorkflowRun) -> "RunContextSnapshot":
        """Build a snapshot from a live ``WorkflowRun`` aggregate."""
        status_value = run.status.value if hasattr(run.status, "value") else str(run.status)
        ctx = run.context
        return cls(
            run_id=run.id,
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
            status=status_value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error=run.error,
            output_data=run.output_data,
            input_data=dict(run.input_data),
            variables=dict(ctx.variables),
            last_output_ports=dict(getattr(ctx, "last_output_ports", {}) or {}),
            last_taken_edge_ports={
                k: dict(v)
                for k, v in (getattr(ctx, "last_taken_edge_ports", {}) or {}).items()
            },
            completed_nodes=frozenset(ctx.completed_nodes),
            skipped_nodes=frozenset(ctx.skipped_nodes),
            topology=ctx.topology,
            child_runs=dict(ctx.child_runs),
            parent_run_id=run.parent_run_id,
            root_run_id=run.root_run_id,
            depth_level=run.depth_level,
        )
