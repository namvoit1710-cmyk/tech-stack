from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.designer_run_result import DesignerRunResult
from app.layer1_domain.entities.workflow_design_state import ConversationTurn
from app.layer2_application.interfaces.progress_sink_interface import IProgressSink


class IWorkflowDesigner(Protocol):
    """Port that designs a workflow from a conversation via an agentic tool loop.

    The implementation runs the LLM in a bounded think -> call-tool -> observe loop: it
    discovers the live node palette + config schemas from the control-plane API and builds
    a candidate workflow graph through repeated tool calls, streaming every step (thinking,
    tool calls, tool results) through ``progress``. There is no hardcoded node/config
    assembly and no deterministic fallback. The designer only DESIGNS — it returns the raw
    candidate definition (or a clarifying question, or an error); it does not decide the
    final user-facing outcome. A ``ValidateRepairGate`` (Layer2) owns validate -> persist ->
    dry-run -> repair -> test-run -> finalize on the returned candidate.
    """

    async def run(
        self,
        message: str,
        history: list[ConversationTurn],
        progress: IProgressSink | None = None,
        current_workflow: dict | None = None,
    ) -> DesignerRunResult: ...
