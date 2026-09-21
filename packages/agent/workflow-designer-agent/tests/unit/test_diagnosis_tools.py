from __future__ import annotations

from app.layer1_domain.entities.diagnosis_verdict import DiagnosisVerdict
from app.layer4_frameworks.tools.diagnosis_tools import build_diagnosis_tools


class FakeReader:
    def __init__(self, run_id="run-9", log=None):
        self._run_id = run_id
        self._log = log if log is not None else {"run_id": run_id, "workflow_id": "wf-7"}
        self.resolved_with: tuple | None = None
        self.log_for: str | None = None

    async def resolve_run_id(self, workflow_id="", workflow_name=""):
        self.resolved_with = (workflow_id, workflow_name)
        return self._run_id

    async def get_execution_log(self, run_id, offset=0, include_children=True):
        self.log_for = run_id
        return {**self._log, "run_id": run_id}


class FakeDiagnoser:
    def __init__(self, verdict):
        self._verdict = verdict
        self.called_with: tuple | None = None

    async def diagnose(self, execution_log, run_id=""):
        self.called_with = (execution_log, run_id)
        return self._verdict


def _tool(reader, diagnoser):
    (diagnose_failure,) = build_diagnosis_tools(reader, diagnoser)
    return diagnose_failure


async def test_needs_reference_when_nothing_given():
    tool = _tool(FakeReader(), FakeDiagnoser(DiagnosisVerdict(category="triggering")))
    result = await tool.ainvoke({})
    assert result["status"] == "needs_reference"


async def test_resolves_run_from_name_then_diagnoses():
    reader = FakeReader(run_id="run-9", log={"workflow_id": "wf-7"})
    verdict = DiagnosisVerdict(
        run_id="run-9", category="agent_service_communication", failed_node_ids=["b"],
        root_cause="HTTP 500", recommended_fix="point at a reachable URL",
    )
    tool = _tool(reader, FakeDiagnoser(verdict))

    result = await tool.ainvoke({"workflow_name": "Weather"})

    assert reader.resolved_with == ("", "Weather")     # resolved by name
    assert reader.log_for == "run-9"                    # log fetched for the resolved run
    assert result["status"] == "diagnosed"
    assert result["workflow_id"] == "wf-7"              # taken from the run's log
    assert result["failed_node_ids"] == ["b"]
    assert result["recommended_fix"] == "point at a reachable URL"


async def test_no_failure_reports_nothing_to_repair():
    verdict = DiagnosisVerdict(run_id="run-9", category=DiagnosisVerdict.NO_FAILURE)
    tool = _tool(FakeReader(), FakeDiagnoser(verdict))
    result = await tool.ainvoke({"run_id": "run-9"})
    assert result["status"] == "no_failure"


async def test_not_found_when_run_cannot_be_resolved():
    reader = FakeReader(run_id="")     # nothing resolves
    tool = _tool(reader, FakeDiagnoser(DiagnosisVerdict(category="triggering")))
    result = await tool.ainvoke({"workflow_name": "Ghost"})
    assert result["status"] == "not_found"


async def test_explicit_run_id_skips_resolution():
    reader = FakeReader(log={"workflow_id": "wf-7"})
    verdict = DiagnosisVerdict(run_id="run-42", category="triggering", failed_node_ids=["a"])
    diagnoser = FakeDiagnoser(verdict)
    tool = _tool(reader, diagnoser)

    result = await tool.ainvoke({"run_id": "run-42"})

    assert reader.resolved_with is None                 # no resolution needed
    assert diagnoser.called_with[1] == "run-42"         # diagnosed for the given run
    assert result["run_id"] == "run-42"
