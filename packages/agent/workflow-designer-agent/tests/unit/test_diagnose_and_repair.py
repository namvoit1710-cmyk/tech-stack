from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.diagnosis_verdict import DiagnosisVerdict
from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult
from app.layer1_domain.entities.workflow_run import DryRunOutcome, RunOutcome, WorkflowRef
from app.layer1_domain.interfaces.workflow_control_plane_interface import (
    WorkflowControlPlaneError,
)
from app.layer2_application.features.workflow_design.use_cases.diagnose_and_repair import (
    DiagnoseAndRepair,
)
from app.layer2_application.features.workflow_design.use_cases.validate_repair_gate import (
    ValidateRepairGate,
)

BASE = {
    "name": "greet", "description": "d",
    "nodes": [
        {"id": "a", "type": "TRIGGER", "name": "A", "config": {}},
        {"id": "b", "type": "TASK", "name": "B", "config": {"url": "https://broken"}},
        {"id": "c", "type": "OUTPUT", "name": "C", "config": {}},
    ],
    "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
}


class FakeReader:
    def __init__(self, run_id="run-9", log=None):
        self._run_id = run_id
        self._log = log if log is not None else {"run_id": run_id, "workflow_id": "wf-7"}

    async def resolve_run_id(self, workflow_id="", workflow_name=""):
        return self._run_id

    async def get_execution_log(self, run_id, offset=0, include_children=True):
        return {**self._log, "run_id": run_id}


class FakeDiagnoser:
    def __init__(self, verdict):
        self._verdict = verdict

    async def diagnose(self, execution_log, run_id=""):
        return self._verdict


class FakeCP:
    def __init__(self, base, dry_runs=None, test_runs=None):
        self._base = base
        self._dry = list(dry_runs or [DryRunOutcome(ok=True)])
        self._test = list(test_runs or [RunOutcome(status="COMPLETED", run_id="r-1")])
        self.patched: list = []
        self.created: list = []

    async def get(self, workflow_id):
        return dict(self._base)

    async def create(self, definition):
        self.created.append(definition)
        return WorkflowRef(workflow_id="wf-new")

    async def update(self, workflow_id, definition):
        return WorkflowRef(workflow_id=workflow_id)

    async def patch(self, workflow_id, definition):
        self.patched.append((workflow_id, definition))
        return WorkflowRef(workflow_id=workflow_id)

    async def dry_run(self, workflow_id, input_data=None):
        return self._dry.pop(0) if len(self._dry) > 1 else self._dry[0]

    async def test_run(self, workflow_id, input_data, skip_validation=True):
        return self._test.pop(0) if len(self._test) > 1 else self._test[0]


class NoRepair:
    async def repair(self, definition, error_message, error_details):
        return definition


def _use_case(cp, verdict, run_id="run-9", log=None):
    reader = FakeReader(run_id=run_id, log=log)
    gate = ValidateRepairGate(cp, NoRepair())
    return DiagnoseAndRepair(reader, FakeDiagnoser(verdict), cp, gate)


async def test_asks_for_a_run_reference_when_nothing_is_given():
    # A1 integrated: no workflow id/name and no run id -> a clarifying question, no work done.
    cp = FakeCP(BASE)
    uc = _use_case(cp, DiagnosisVerdict(category="triggering"))
    result = await uc.run()
    assert result.status == WorkflowDesignResult.NEEDS_CLARIFICATION
    assert "run_id" in result.content.lower()
    assert not cp.patched


async def test_reports_no_repair_needed_when_run_did_not_fail():
    cp = FakeCP(BASE)
    verdict = DiagnosisVerdict(run_id="run-9", category=DiagnosisVerdict.NO_FAILURE)
    uc = _use_case(cp, verdict)
    result = await uc.run(workflow_id="wf-7")
    assert result.status == WorkflowDesignResult.SUCCESS
    assert "no failure" in result.content.lower()
    assert not cp.patched


async def test_diagnoses_then_auto_repairs_via_gate_update_mode():
    # C1: a Class-A dry-run failure is auto-repaired; persistence is PATCH (id stable); the
    # final test-run passes.
    cp = FakeCP(BASE, dry_runs=[
        DryRunOutcome(ok=False, error_code="INVALID_EDGE_REFERENCE", error_message="bad edge"),
        DryRunOutcome(ok=True),
    ])
    verdict = DiagnosisVerdict(
        run_id="run-9", category="agent_service_communication",
        failed_node_ids=["b"], root_cause="agent_service_communication: HTTP 500",
        recommended_fix="point the node at a reachable URL",
    )
    uc = _use_case(cp, verdict)
    result = await uc.run(workflow_id="wf-7")

    assert result.status == WorkflowDesignResult.SUCCESS
    assert result.workflow_id == "wf-7"                 # id stayed stable
    assert len(cp.patched) == 2 and not cp.created       # persisted via PATCH across repair


async def test_class_b_failure_asks_the_user_instead_of_looping():
    # C2: a Class-B failure (value only the user knows) stops with one clarifying question.
    cp = FakeCP(BASE, dry_runs=[
        DryRunOutcome(ok=False, error_code="SERVICE_NOT_CONFIGURED", error_message="no client"),
    ])
    verdict = DiagnosisVerdict(
        run_id="run-9", category="creation_configuration", failed_node_ids=["b"],
    )
    uc = _use_case(cp, verdict)
    result = await uc.run(workflow_id="wf-7")

    assert result.status == WorkflowDesignResult.NEEDS_CLARIFICATION


async def test_success_report_names_root_cause_solution_and_fixed_nodes():
    # D1: the user-facing content names the root cause, the solution, and the changed node ids.
    cp = FakeCP(BASE)
    verdict = DiagnosisVerdict(
        run_id="run-9", category="agent_service_communication", failed_node_ids=["b"],
        root_cause="agent_service_communication: HTTP 500 from the worker",
        recommended_fix="point the node at a reachable URL",
    )
    uc = _use_case(cp, verdict)
    result = await uc.run(workflow_id="wf-7")

    assert result.status == WorkflowDesignResult.SUCCESS
    content = result.content
    assert "HTTP 500 from the worker" in content          # root cause
    assert "point the node at a reachable URL" in content  # solution
    assert "b" in content                                  # fixed point (node id)


async def test_derives_workflow_id_from_run_when_only_run_id_given():
    # Given only a run_id, the workflow to repair is taken from the run's own record.
    cp = FakeCP(BASE)
    verdict = DiagnosisVerdict(run_id="run-9", category="triggering", failed_node_ids=["a"],
                              root_cause="never started", recommended_fix="fix trigger")
    uc = _use_case(cp, verdict, run_id="run-9", log={"run_id": "run-9", "workflow_id": "wf-7"})
    result = await uc.run(run_id="run-9")
    assert result.status == WorkflowDesignResult.SUCCESS
