from __future__ import annotations

from typing import Any

from app.layer2_application.features.workflow_design.use_cases.resolve_failed_run import (
    ResolveFailedRun,
    run_reference_clarification,
)


class _FakeReader:
    """Records calls; mirrors the troubleshooting-agent IWorkflowClient read contract."""

    def __init__(self, run_id: str = "run-9") -> None:
        self._run_id = run_id
        self.calls: list[tuple] = []

    async def resolve_run_id(self, workflow_id: str = "", workflow_name: str = "") -> str:
        self.calls.append(("resolve", workflow_id, workflow_name))
        return self._run_id

    async def get_execution_log(
        self, run_id: str, offset: int = 0, include_children: bool = True
    ) -> dict[str, Any]:
        self.calls.append(("log", run_id))
        return {"run_id": run_id, "summary": {"node_counts": {"failed": 1}}}


async def test_resolves_run_from_workflow_name_then_fetches_log():
    # A2: given only a workflow_name, resolve the run then fetch its execution log.
    reader = _FakeReader(run_id="run-9")
    result = await ResolveFailedRun(reader).resolve(workflow_name="Weather flow")

    assert result.run_id == "run-9"
    assert result.execution_log["run_id"] == "run-9"
    # Resolution happened via resolve_run_id(name), then the log via get_execution_log(id).
    assert reader.calls == [("resolve", "", "Weather flow"), ("log", "run-9")]


async def test_returns_empty_and_skips_log_when_no_run_found():
    # No run resolvable -> don't fetch a log for a nonexistent run.
    reader = _FakeReader(run_id="")
    result = await ResolveFailedRun(reader).resolve(workflow_name="ghost")

    assert not result.found()
    assert result.execution_log == {}
    assert reader.calls == [("resolve", "", "ghost")]  # get_execution_log NOT called


def test_asks_for_run_ref_when_none_given():
    # A1: no run reference at all -> a clarify question naming both options.
    question = run_reference_clarification()
    assert question is not None
    assert "run_id" in question.lower()
    assert "workflow name" in question.lower() or "workflow_name" in question.lower()


def test_no_clarification_when_a_run_ref_is_present():
    assert run_reference_clarification(run_id="run-1") is None
    assert run_reference_clarification(workflow_name="Weather") is None
    assert run_reference_clarification(workflow_id="wf-1") is None
