from __future__ import annotations

from app.layer1_domain.entities.repair_policy import RepairPolicy
from app.layer1_domain.entities.workflow_design_result import WorkflowDesignResult
from app.layer1_domain.entities.workflow_run import DryRunOutcome, RunOutcome, WorkflowRef
from app.layer1_domain.interfaces.workflow_control_plane_interface import WorkflowControlPlaneError
from app.layer2_application.features.workflow_design.use_cases.validate_repair_gate import (
    ValidateRepairGate,
)
from app.layer2_application.interfaces.workflow_repairer_interface import WorkflowRepairError

DEFINITION = {
    "name": "wf", "description": "d",
    "nodes": [{"id": "a", "type": "TRIGGER", "name": "A"}, {"id": "b", "type": "OUTPUT", "name": "B"}],
    "edges": [{"source": "a", "target": "b"}],
}


class FakeControlPlane:
    """Scripted control plane: dry_run / test_run outcomes are consumed in order."""

    def __init__(self, dry_runs=None, test_runs=None, create_error=None, base=None) -> None:
        self._dry_runs = list(dry_runs or [DryRunOutcome(ok=True)])
        self._test_runs = list(test_runs or [RunOutcome(status="COMPLETED", run_id="r-1")])
        self._create_error = create_error
        self._base = base  # what get() returns (the loaded existing workflow)
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.patched: list[tuple[str, dict]] = []
        self.got: list[str] = []
        self.test_run_calls: list[tuple[str, bool]] = []

    async def get(self, workflow_id: str) -> dict:
        self.got.append(workflow_id)
        return dict(self._base) if self._base is not None else {"name": "wf", "nodes": [], "edges": []}

    async def create(self, definition: dict) -> WorkflowRef:
        if self._create_error:
            raise self._create_error
        self.created.append(definition)
        return WorkflowRef(workflow_id="wf-1", link="/workflow/wf-1")

    async def update(self, workflow_id: str, definition: dict) -> WorkflowRef:
        self.updated.append((workflow_id, definition))
        return WorkflowRef(workflow_id=workflow_id, link=f"/workflow/{workflow_id}")

    async def patch(self, workflow_id: str, definition: dict) -> WorkflowRef:
        self.patched.append((workflow_id, definition))
        return WorkflowRef(workflow_id=workflow_id, link=f"/workflow/{workflow_id}")

    async def dry_run(self, workflow_id: str, input_data=None) -> DryRunOutcome:
        return self._dry_runs.pop(0) if len(self._dry_runs) > 1 else self._dry_runs[0]

    async def test_run(self, workflow_id: str, input_data: dict, skip_validation: bool = True) -> RunOutcome:
        self.test_run_calls.append((workflow_id, skip_validation))
        return self._test_runs.pop(0) if len(self._test_runs) > 1 else self._test_runs[0]


class FakeRepairer:
    def __init__(self, fixed_definition: dict | None = None, error: Exception | None = None) -> None:
        self._fixed = fixed_definition
        self._error = error
        self.calls: list[tuple[dict, str, dict]] = []

    async def repair(self, definition: dict, error_message: str, error_details: dict) -> dict:
        self.calls.append((definition, error_message, error_details))
        if self._error:
            raise self._error
        return self._fixed or definition


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event, data) -> None:
        self.events.append((event, data))


async def test_finalize_succeeds_on_first_try_with_one_final_test_run() -> None:
    cp = FakeControlPlane()
    gate = ValidateRepairGate(cp, FakeRepairer())
    result = await gate.finalize(DEFINITION, "")
    assert result.status == WorkflowDesignResult.SUCCESS
    assert result.workflow_id == "wf-1"
    assert len(cp.created) == 1
    assert cp.test_run_calls == [("wf-1", False)]  # exactly one test/full, skip_validation False


async def test_finalize_updates_an_existing_workflow_via_get_merge_patch() -> None:
    cp = FakeControlPlane()
    gate = ValidateRepairGate(cp, FakeRepairer())
    result = await gate.finalize(DEFINITION, "wf-existing")
    assert result.status == WorkflowDesignResult.SUCCESS
    assert result.content == "Updated your workflow."
    assert cp.got == ["wf-existing"]                 # loaded the base first
    assert cp.patched[0][0] == "wf-existing"          # persisted via PATCH (id stable)
    assert not cp.created and not cp.updated          # not a create, not a full-replace PUT


async def test_finalize_update_preserves_unrelated_nodes_and_edges() -> None:
    # AC B1: base has 3 nodes a->b->c; the change only re-emits b (config edit). a, c and the
    # untouched edges must survive without the model re-sending them (AC B2).
    base = {
        "name": "wf", "description": "d",
        "nodes": [
            {"id": "a", "type": "TRIGGER", "name": "A", "config": {}},
            {"id": "b", "type": "TASK", "name": "B", "config": {"x": 1}},
            {"id": "c", "type": "OUTPUT", "name": "C", "config": {}},
        ],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
    }
    change = {  # only the changed node, no edges re-emitted
        "name": "wf", "description": "d",
        "nodes": [{"id": "b", "type": "TASK", "name": "B2", "config": {"x": 2}}],
        "edges": [],
    }
    cp = FakeControlPlane(base=base)
    gate = ValidateRepairGate(cp, FakeRepairer())

    result = await gate.finalize(change, "wf-existing")

    assert result.status == WorkflowDesignResult.SUCCESS
    persisted = cp.patched[0][1]
    by_id = {n["id"]: n for n in persisted["nodes"]}
    assert set(by_id) == {"a", "b", "c"}                 # unrelated a, c preserved
    assert by_id["b"]["name"] == "B2" and by_id["b"]["config"] == {"x": 2}  # change applied
    edge_pairs = {(e["source"], e["target"]) for e in persisted["edges"]}
    assert edge_pairs == {("a", "b"), ("b", "c")}        # untouched edges preserved


async def test_finalize_update_removes_a_node_and_its_edges() -> None:
    base = {
        "name": "wf", "description": "d",
        "nodes": [
            {"id": "a", "type": "TRIGGER", "name": "A", "config": {}},
            {"id": "b", "type": "TASK", "name": "B", "config": {}},
            {"id": "c", "type": "OUTPUT", "name": "C", "config": {}},
        ],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
    }
    change = {  # re-wire a->c directly; delete b
        "name": "wf", "description": "d",
        "nodes": [], "edges": [{"source": "a", "target": "c"}],
    }
    cp = FakeControlPlane(base=base)
    gate = ValidateRepairGate(cp, FakeRepairer())

    result = await gate.finalize(change, "wf-existing", remove_node_ids=["b"])

    assert result.status == WorkflowDesignResult.SUCCESS
    persisted = cp.patched[0][1]
    assert {n["id"] for n in persisted["nodes"]} == {"a", "c"}   # b removed
    edge_pairs = {(e["source"], e["target"]) for e in persisted["edges"]}
    assert edge_pairs == {("a", "c")}                            # b's edges gone, new edge kept


async def test_finalize_update_repair_re_persists_via_patch() -> None:
    # A dry-run failure on an existing update repairs, then re-persists — still via PATCH,
    # never a create (the id must stay stable across repair rounds).
    base = {"name": "wf", "nodes": [
        {"id": "a", "type": "TRIGGER", "name": "A"}, {"id": "b", "type": "OUTPUT", "name": "B"}],
        "edges": [{"source": "a", "target": "b"}]}
    cp = FakeControlPlane(base=base, dry_runs=[
        DryRunOutcome(ok=False, error_code="INVALID_EDGE_REFERENCE", error_message="bad edge"),
        DryRunOutcome(ok=True),
    ])
    gate = ValidateRepairGate(cp, FakeRepairer(fixed_definition=DEFINITION))

    result = await gate.finalize(DEFINITION, "wf-existing")

    assert result.status == WorkflowDesignResult.SUCCESS
    assert len(cp.patched) == 2 and not cp.created and not cp.updated


async def test_finalize_clones_source_via_get_merge_create() -> None:
    # SA-2131: a source_workflow_id loads the SOURCE, merges the change, and persists a NEW
    # workflow via create() — never touching the source (no patch/update on it).
    base = {
        "name": "src", "description": "d",
        "nodes": [
            {"id": "a", "type": "TRIGGER", "name": "A", "config": {}},
            {"id": "b", "type": "OUTPUT", "name": "B", "config": {}},
        ],
        "edges": [{"source": "a", "target": "b"}],
    }
    cp = FakeControlPlane(base=base)
    gate = ValidateRepairGate(cp, FakeRepairer())

    change = {"name": "copy of src", "description": "d", "nodes": [], "edges": []}
    result = await gate.finalize(change, "", source_workflow_id="wf-src")

    assert result.status == WorkflowDesignResult.SUCCESS
    assert result.content == "Created your workflow."       # a clone IS a creation
    assert result.workflow_id == "wf-1"                      # NEW id from create()
    assert cp.got == ["wf-src"]                              # loaded the SOURCE as template
    assert len(cp.created) == 1                              # persisted via create()
    assert not cp.patched and not cp.updated                # source (and nothing) was written
    created = cp.created[0]
    assert created["name"] == "copy of src"
    assert {n["id"] for n in created["nodes"]} == {"a", "b"}  # source graph copied into the clone


async def test_finalize_clone_applies_change_and_leaves_source_unwritten() -> None:
    base = {
        "name": "src", "description": "d",
        "nodes": [
            {"id": "a", "type": "TRIGGER", "name": "A", "config": {}},
            {"id": "b", "type": "TASK", "name": "B", "config": {"to": "email"}},
            {"id": "c", "type": "OUTPUT", "name": "C", "config": {}},
        ],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
    }
    cp = FakeControlPlane(base=base)
    gate = ValidateRepairGate(cp, FakeRepairer())

    # change only node b (email -> slack); no edges re-emitted, a and c untouched
    change = {
        "name": "src (slack)", "description": "d",
        "nodes": [{"id": "b", "type": "TASK", "name": "B", "config": {"to": "slack"}}],
        "edges": [],
    }
    result = await gate.finalize(change, "", source_workflow_id="wf-src")

    assert result.status == WorkflowDesignResult.SUCCESS
    assert not cp.patched and not cp.updated                 # SOURCE never mutated
    created = cp.created[0]
    by_id = {n["id"]: n for n in created["nodes"]}
    assert set(by_id) == {"a", "b", "c"}                     # unrelated a, c copied
    assert by_id["b"]["config"] == {"to": "slack"}           # requested change applied in the clone
    edge_pairs = {(e["source"], e["target"]) for e in created["edges"]}
    assert edge_pairs == {("a", "b"), ("b", "c")}            # untouched wiring copied


async def test_finalize_repairs_a_structural_error_then_succeeds() -> None:
    bad = {"name": "wf", "description": "", "nodes": [{"id": "a", "type": "T", "name": "A"}], "edges": []}
    fixed = DEFINITION
    cp = FakeControlPlane()
    repairer = FakeRepairer(fixed_definition=fixed)
    gate = ValidateRepairGate(cp, repairer)
    sink = RecordingSink()

    result = await gate.finalize(bad, "", sink)

    assert result.status == WorkflowDesignResult.SUCCESS
    assert len(repairer.calls) == 1
    assert "at least" in repairer.calls[0][1]  # WorkflowValidationError message
    assert [e for e, _ in sink.events] == ["repair_iteration"]
    assert sink.events[0][1]["blocked"] is False


async def test_finalize_repairs_a_semantic_authoring_error() -> None:
    repairer = FakeRepairer(fixed_definition=DEFINITION)

    class OnceThenOkControlPlane(FakeControlPlane):
        async def create(self, definition):
            if not self.created:
                self.created.append(definition)
                raise WorkflowControlPlaneError("INVALID_NODE_DATA", "bad node")
            return WorkflowRef(workflow_id="wf-2", link="/workflow/wf-2")

    cp = OnceThenOkControlPlane()
    gate = ValidateRepairGate(cp, repairer)

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.SUCCESS
    assert result.workflow_id == "wf-2"
    assert repairer.calls[0][1] == "bad node"


async def test_finalize_repairs_a_dry_run_failure() -> None:
    cp = FakeControlPlane(dry_runs=[
        DryRunOutcome(ok=False, error_code="INVALID_EDGE_REFERENCE", error_message="bad edge"),
        DryRunOutcome(ok=True),
    ])
    repairer = FakeRepairer(fixed_definition=DEFINITION)
    gate = ValidateRepairGate(cp, repairer)

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.SUCCESS
    assert repairer.calls[0][1] == "bad edge"


async def test_finalize_feeds_a_failed_final_run_back_as_one_more_repair_round() -> None:
    cp = FakeControlPlane(test_runs=[
        RunOutcome(status="FAILED", error="node exploded", error_code="INVALID_NODE_DATA"),
        RunOutcome(status="COMPLETED", run_id="r-2"),
    ])
    repairer = FakeRepairer(fixed_definition=DEFINITION)
    gate = ValidateRepairGate(cp, repairer)

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.SUCCESS
    assert result.content == "Repaired and created your workflow."
    assert repairer.calls[0][1] == "node exploded"
    assert len(cp.test_run_calls) == 2


async def test_finalize_returns_needs_clarification_for_class_b_error() -> None:
    cp = FakeControlPlane(create_error=WorkflowControlPlaneError("SERVICE_NOT_CONFIGURED", "no client"))
    gate = ValidateRepairGate(cp, FakeRepairer())
    sink = RecordingSink()

    result = await gate.finalize(DEFINITION, "", sink)

    assert result.status == WorkflowDesignResult.NEEDS_CLARIFICATION
    assert "no client" in result.content
    assert sink.events[0][1]["blocked"] is True
    # the real graph must survive on the result — NOT an empty {} — so a caller that persists
    # the conversation's "current workflow" from this result doesn't wipe out what was built.
    assert result.workflow == DEFINITION


async def test_finalize_exhausted_result_carries_the_last_definition() -> None:
    cp = FakeControlPlane(create_error=WorkflowControlPlaneError("VALIDATION_ERROR", "always broken"))
    repairer = FakeRepairer(fixed_definition=DEFINITION)
    gate = ValidateRepairGate(cp, repairer, RepairPolicy(max_attempts=1))

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.ERROR
    assert result.workflow == DEFINITION


async def test_finalize_exhausts_budget_and_returns_actionable_error() -> None:
    cp = FakeControlPlane(create_error=WorkflowControlPlaneError("VALIDATION_ERROR", "always broken"))
    repairer = FakeRepairer(fixed_definition=DEFINITION)  # "fix" never actually clears the error
    gate = ValidateRepairGate(cp, repairer, RepairPolicy(max_attempts=2))

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.ERROR
    assert "always broken" in result.content
    assert "2 attempt" in result.content
    assert len(repairer.calls) == 2


async def test_finalize_cyclic_nesting_stops_with_a_specific_question_not_a_repair_loop() -> None:
    # SA-2132 AC2: a cross-workflow cycle surfaces at test-run with no tabled error_code. It must
    # stop with a SPECIFIC, user-actionable question (Class B) and must NOT be handed to the
    # repairer to loop on (a cycle is a design choice, not a mechanical fix).
    cp = FakeControlPlane(test_runs=[
        RunOutcome(status="FAILED",
                   error="Cross-workflow cycle detected: workflow 'wf-1' already appears in the ancestry chain"),
    ])
    repairer = FakeRepairer(fixed_definition=DEFINITION)
    gate = ValidateRepairGate(cp, repairer)

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.NEEDS_CLARIFICATION
    assert "cycle" in result.content.lower()
    assert "provide the correct value" not in result.content.lower()
    assert repairer.calls == []          # a cycle is never auto-repaired
    assert result.workflow == DEFINITION  # the built graph survives on the result


async def test_finalize_max_nesting_depth_stops_with_a_specific_question() -> None:
    # SA-2132 AC2: exceeding the platform's max nesting depth is also terminal Class B.
    cp = FakeControlPlane(test_runs=[
        RunOutcome(status="FAILED", error="Max nesting depth 10 exceeded at node 'subwf_1'"),
    ])
    repairer = FakeRepairer(fixed_definition=DEFINITION)
    gate = ValidateRepairGate(cp, repairer)

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.NEEDS_CLARIFICATION
    assert "deep" in result.content.lower() or "nesting" in result.content.lower()
    assert repairer.calls == []


async def test_finalize_stops_when_repairer_itself_fails() -> None:
    cp = FakeControlPlane(create_error=WorkflowControlPlaneError("VALIDATION_ERROR", "broken"))
    repairer = FakeRepairer(error=WorkflowRepairError("LLM produced garbage"))
    gate = ValidateRepairGate(cp, repairer)

    result = await gate.finalize(DEFINITION, "")

    assert result.status == WorkflowDesignResult.ERROR
    assert len(repairer.calls) == 1  # fails fast, does not burn the whole budget guessing
