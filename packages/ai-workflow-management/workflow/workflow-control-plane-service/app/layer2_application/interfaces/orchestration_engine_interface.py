"""Interface for the orchestration engine — used by layer2_application use cases.

Use cases must depend on this Protocol, not the concrete OrchestrationEngine
class in layer1_domain, to keep the clean-architecture dependency direction
(layer2 → layer1 via interface, not direct concrete import).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IOrchestrationEngine(Protocol):
    """Protocol satisfied by OrchestrationEngine (structural sub-typing)."""

    # ------------------------------------------------------------------
    # Core orchestration methods
    # ------------------------------------------------------------------

    async def handle_entry_nodes(
        self,
        entry_node_ids: list,
        run: Any,
        workflow: Any,
        topology: Any,
        task_factory: Any,
    ) -> None: ...

    async def process_successors(
        self, task: Any, run: Any, workflow: Any, topology: Any
    ) -> Any: ...

    async def process_and_dispatch_successors(
        self, completed_task: Any, run: Any, workflow: Any,
        topology: Any, test_mode: Any = None,
    ) -> Any: ...

    async def check_run_completion(self, run: Any, workflow: Any) -> None: ...

    async def orchestrate_resume_run(self, run_id: str) -> None: ...

    async def orchestrate_check_completion(self, run_id: str) -> None: ...

    async def force_fail_run(self, run_id: str, error_message: str) -> None: ...

    async def drain_events(self) -> None: ...

    async def maybe_enqueue_loop_iteration(self, task: Any, run: Any) -> None: ...

    async def fail_parent_tracking_task(self, child_run: Any) -> None: ...

    def publish_event(self, topic: str, event: Any) -> None: ...

    def publish_events(self, events: list) -> None: ...

    def publish_callback(self, run: Any, status: str) -> None: ...

    def is_builtin_node(self, node_id: str, workflow: Any) -> bool: ...

    def apply_state_updates(
        self, state_updates: Any, run: Any, workflow: Any
    ) -> None: ...

    async def apply_state_updates_as_files(
        self,
        state_updates: Any,
        run: Any,
        workflow: Any,
        *,
        source_node_id: str = "",
        causation_id: str = "",
        output_port: str = "",
        taken_edge_ports: dict[str, str] | None = None,
        attempt_id: str = "",
    ) -> None:
        """Emit ``NodeOutputProduced`` (carrying the full write-set
        including name aliases) and apply the updates to
        ``run.context.variables``. Phase 0 guarantees emission happens
        BEFORE mutation so the event stream is authoritative.

        Phase 1a: use cases that used to write directly to
        ``run.context.variables`` (``complete_task``, ``fail_task``)
        route through this method so the projector can observe the
        write via the event log.
        """
        ...

    def drop_run_resolver_cache(self, run_id: str) -> None: ...

    # ------------------------------------------------------------------
    # Semantic event-emission helpers
    # ------------------------------------------------------------------
    # Each method owns the event construction so use cases never need to
    # import EventTopic constants or event dataclasses directly.

    # Run lifecycle — unified RunStatusChanged emitter
    def emit_run_status(
        self,
        run: Any,
        status: str,
        *,
        input_data: dict | None = None,
        output_data: dict | None = None,
        error: str = "",
        failed_task_id: str = "",
        cancelled_by: str = "",
        reason: str = "",
    ) -> None: ...

    def emit_variables_seeded(
        self, run: Any, state_updates: dict, *, seed_id: str = "variables_seeded",
    ) -> None: ...

    # Node lifecycle — uniform node-jump spine
    def emit_node_dispatched(
        self,
        task: Any,
        run: Any,
        node: Any = None,
        *,
        attempt: int = 1,
        dispatch_reason: str = "",
    ) -> None: ...

    def emit_node_completed(
        self, task: Any, run: Any, node: Any = None
    ) -> None: ...

    def emit_node_failed(
        self,
        task: Any,
        run: Any,
        error_code: str = "WORKER_ERROR",
        error_msg: str = "",
    ) -> None: ...

    def emit_node_cancelled(
        self,
        run: Any,
        *,
        task: Any = None,
        node_id: str = "",
        cancelled_by: str = "",
        reason: str = "",
    ) -> None: ...

    def emit_child_run_cancelled(
        self,
        parent_run: Any,
        child_run: Any,
        *,
        reason: str = "",
    ) -> None: ...

    # HITL (INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD completion)
    def emit_pending_node_completed(
        self, task: Any, run: Any, node: Any, action: str = "confirm"
    ) -> None: ...

    # Data-mutation events
    def emit_artifact_added(
        self,
        run: Any,
        node_id: str,
        file_id: str,
        name: str,
        content_type: str,
        size_bytes: int,
        artifact_type: str,
    ) -> None: ...

    def emit_node_input_edited(
        self,
        run: Any,
        node_id: str,
        task_id: str,
        previous_file_id: str,
        new_file_id: str,
    ) -> None: ...

    def emit_node_data_edited(
        self,
        run: Any,
        workflow: Any,
        *,
        node_id: str,
        new_ref: Any,
        task_id: str = "",
        edited_by: str = "",
        reason: str = "",
    ) -> None: ...
