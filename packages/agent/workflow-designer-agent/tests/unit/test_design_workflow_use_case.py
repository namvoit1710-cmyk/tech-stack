from __future__ import annotations

from app.layer1_domain.entities.designer_run_result import DesignerRunResult
from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult
from app.layer2_application.features.workflow_design.use_cases.design_workflow_use_case import (
    DesignWorkflowUseCase,
)


class FakeDesigner:
    def __init__(self, result: DesignerRunResult) -> None:
        self._result = result
        self.calls: list[tuple] = []

    async def run(self, message, history, progress=None, current_workflow=None) -> DesignerRunResult:
        self.calls.append((message, history, current_workflow))
        return self._result


class FakeGate:
    def __init__(self, result: WorkflowDesignResult) -> None:
        self._result = result
        self.calls: list[tuple] = []

    async def finalize(self, definition, workflow_id, progress=None, remove_node_ids=None,
                       source_workflow_id="") -> WorkflowDesignResult:
        self.calls.append((definition, workflow_id, list(remove_node_ids or []), source_workflow_id))
        return self._result


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event, data) -> None:
        self.events.append((event, data))


async def test_candidate_is_delegated_to_the_gate() -> None:
    candidate = DesignerRunResult(status=DesignerRunResult.CANDIDATE,
                                   definition={"name": "wf"}, workflow_id="wf-1",
                                   remove_node_ids=["gone"])
    gate_result = WorkflowDesignResult(status=WorkflowDesignResult.SUCCESS, content="Created your workflow.")
    gate = FakeGate(gate_result)
    use_case = DesignWorkflowUseCase(FakeDesigner(candidate), gate)

    result = await use_case.design("build a flow")

    assert result.status == WorkflowDesignResult.SUCCESS
    # removals threaded to the gate; source_workflow_id empty for a plain update
    assert gate.calls == [({"name": "wf"}, "wf-1", ["gone"], "")]


async def test_clone_candidate_threads_source_workflow_id_to_the_gate() -> None:
    candidate = DesignerRunResult(status=DesignerRunResult.CANDIDATE,
                                   definition={"name": "copy"}, source_workflow_id="wf-src")
    gate = FakeGate(WorkflowDesignResult(status=WorkflowDesignResult.SUCCESS,
                                         content="Created your workflow."))
    use_case = DesignWorkflowUseCase(FakeDesigner(candidate), gate)

    await use_case.design("clone workflow wf-src but send to slack")

    assert gate.calls == [({"name": "copy"}, "", [], "wf-src")]  # source id reaches the gate


async def test_needs_clarification_short_circuits_the_gate() -> None:
    raw = DesignerRunResult(status=DesignerRunResult.NEEDS_CLARIFICATION, clarify_question="Which file?")
    gate = FakeGate(WorkflowDesignResult())
    use_case = DesignWorkflowUseCase(FakeDesigner(raw), gate)

    result = await use_case.design("build a flow")

    assert result.status == WorkflowDesignResult.NEEDS_CLARIFICATION
    assert result.content == "Which file?"
    assert gate.calls == []  # the gate never runs without a candidate definition


async def test_designer_error_short_circuits_the_gate() -> None:
    raw = DesignerRunResult(status=DesignerRunResult.ERROR, error_message="I couldn't design that.")
    gate = FakeGate(WorkflowDesignResult())
    use_case = DesignWorkflowUseCase(FakeDesigner(raw), gate)

    result = await use_case.design("???")

    assert result.status == WorkflowDesignResult.ERROR
    assert result.content == "I couldn't design that."
    assert gate.calls == []


async def test_final_content_streams_word_by_word() -> None:
    candidate = DesignerRunResult(status=DesignerRunResult.CANDIDATE, definition={"name": "wf"})
    gate_result = WorkflowDesignResult(status=WorkflowDesignResult.SUCCESS, content="Created your workflow.")
    use_case = DesignWorkflowUseCase(FakeDesigner(candidate), FakeGate(gate_result))
    use_case.CONTENT_STREAM_DELAY_S = 0  # keep the test instant
    sink = RecordingSink()

    await use_case.design("build a flow", progress=sink)

    deltas = "".join(d["delta"] for e, d in sink.events if e == "content_delta")
    assert deltas == "Created your workflow."
