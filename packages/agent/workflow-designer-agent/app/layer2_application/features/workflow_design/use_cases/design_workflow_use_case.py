from __future__ import annotations

import asyncio
import re

from app.layer1_domain.entities.designer_run_result import DesignerRunResult
from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult
from app.layer1_domain.entities.workflow_design_state import ConversationTurn
from app.layer2_application.features.workflow_design.use_cases.validate_repair_gate import (
    ValidateRepairGate,
)
from app.layer2_application.interfaces.progress_sink_interface import IProgressSink
from app.layer2_application.interfaces.workflow_designer_interface import IWorkflowDesigner


class DesignWorkflowUseCase:
    """Business entry point for designing a workflow from a conversation.

    The agentic tool loop behind the ``IWorkflowDesigner`` port (Layer4) only DESIGNS: it
    discovers the live catalog and builds a candidate workflow graph through repeated tool
    calls, streaming ``thinking`` / ``tool_call`` / ``tool_result`` progress, then hands back
    a raw ``DesignerRunResult`` (candidate / clarification / error). This use case delegates
    finalizing a candidate to the ``ValidateRepairGate`` (validate -> persist -> dry-run ->
    repair -> test-run), then streams the final human-facing message word-by-word as
    ``content_delta`` events so the UI types it out (the terminal ``done`` event still carries
    the full content for clients that don't render deltas). No framework dependency in Layer2.
    """

    #: Regex that yields each word with its trailing whitespace, so streamed
    #: ``content_delta`` chunks concatenate back to the exact original text.
    _WORD_RE = re.compile(r"\S+\s*")

    #: Delay between streamed words so the final answer visibly types out in the UI
    #: (without it, all ``content_delta`` events flush in one network chunk).
    CONTENT_STREAM_DELAY_S = 0.035

    def __init__(self, designer: IWorkflowDesigner, gate: ValidateRepairGate) -> None:
        self._designer = designer
        self._gate = gate

    async def design(
        self,
        message: str,
        history: list[ConversationTurn] | None = None,
        progress: IProgressSink | None = None,
        current_workflow: dict | None = None,
    ) -> WorkflowDesignResult:
        raw = await self._designer.run(message, history or [], progress, current_workflow)
        result = await self._resolve(raw, progress)
        return await self._finalize(result, progress)

    async def _resolve(
        self, raw: DesignerRunResult, progress: IProgressSink | None
    ) -> WorkflowDesignResult:
        if raw.status == DesignerRunResult.NEEDS_CLARIFICATION:
            return WorkflowDesignResult(
                status=WorkflowDesignResult.NEEDS_CLARIFICATION,
                content=raw.clarify_question or "Could you give me a bit more detail?",
            )
        if raw.status == DesignerRunResult.ERROR:
            return WorkflowDesignResult(
                status=WorkflowDesignResult.ERROR,
                content=raw.error_message or "I couldn't design a workflow for that request.",
            )
        return await self._gate.finalize(
            raw.definition, raw.workflow_id, progress, raw.remove_node_ids,
            source_workflow_id=raw.source_workflow_id,
        )

    async def _finalize(
        self, result: WorkflowDesignResult, progress: IProgressSink | None
    ) -> WorkflowDesignResult:
        if progress is not None and result.content:
            words = self._WORD_RE.findall(result.content)
            for index, word in enumerate(words):
                await progress.emit("content_delta", {"delta": word})
                # Pace so the client types it out; no trailing delay after the last word.
                if self.CONTENT_STREAM_DELAY_S and index < len(words) - 1:
                    await asyncio.sleep(self.CONTENT_STREAM_DELAY_S)
        return result
