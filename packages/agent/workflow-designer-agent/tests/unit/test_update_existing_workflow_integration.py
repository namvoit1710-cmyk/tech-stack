"""SA-2157 end-to-end (Overall AC): update an existing workflow by id via NL, driving the real
designer + use case + gate, so load -> diff -> validate/dry-run -> PATCH-persist works together.

A scripted LLM plays the model; the control plane is an in-memory fake that stores the workflow,
so the assertions are made against what actually gets persisted."""
from __future__ import annotations

from app.layer1_domain.entities.catalog import CatalogNode
from app.layer1_domain.entities.workflow_run import DryRunOutcome, RunOutcome, WorkflowRef
from app.layer2_application.features.workflow_design.use_cases.design_workflow_use_case import (
    DesignWorkflowUseCase,
)
from app.layer2_application.features.workflow_design.use_cases.validate_repair_gate import (
    ValidateRepairGate,
)
from app.layer4_frameworks.designer.tool_loop_designer import ToolLoopWorkflowDesigner
from app.layer4_frameworks.tools.tool_registry import build_local_tools


class FakeAI:
    def __init__(self, content="", tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class ScriptedClient:
    def __init__(self, script) -> None:
        self._script = script
        self._i = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        msg = self._script[self._i]
        self._i += 1
        return msg

    async def astream(self, messages):
        msg = self._script[self._i]
        self._i += 1
        yield msg


class FakeLLM:
    def __init__(self, client) -> None:
        self._client = client

    def get_chat_client(self):
        return self._client


class FakeCatalog:
    def __init__(self, nodes) -> None:
        self._nodes = nodes

    async def nodes(self):
        return self._nodes

    async def node_schemas(self, refs):
        return []

    async def workers(self):
        return []


class InMemoryControlPlane:
    """Stores workflows in-process so get() returns the base and patch() persists the merge."""

    def __init__(self, seed: dict) -> None:
        self.store = {k: dict(v) for k, v in seed.items()}
        self.patched: list[tuple[str, dict]] = []
        self.created: list[dict] = []

    async def get(self, workflow_id: str) -> dict:
        return dict(self.store[workflow_id])

    async def create(self, definition: dict) -> WorkflowRef:
        self.created.append(definition)
        self.store["wf-new"] = definition
        return WorkflowRef(workflow_id="wf-new", link="/workflow/wf-new")

    async def update(self, workflow_id: str, definition: dict) -> WorkflowRef:
        self.store[workflow_id] = definition
        return WorkflowRef(workflow_id=workflow_id, link=f"/workflow/{workflow_id}")

    async def patch(self, workflow_id: str, definition: dict) -> WorkflowRef:
        self.patched.append((workflow_id, definition))
        self.store[workflow_id] = definition
        return WorkflowRef(workflow_id=workflow_id, link=f"/workflow/{workflow_id}")

    async def dry_run(self, workflow_id: str, input_data=None) -> DryRunOutcome:
        return DryRunOutcome(ok=True)

    async def test_run(self, workflow_id: str, input_data: dict, skip_validation: bool = True) -> RunOutcome:
        return RunOutcome(status="COMPLETED", run_id="r-1")


def _call(name, args, cid):
    return {"name": name, "args": args, "id": cid}


A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"


async def test_update_existing_workflow_by_id_end_to_end() -> None:
    # A loaded workflow's nodes already carry UUID ids (as the control plane stores them).
    base = {
        "name": "greet", "description": "d",
        "nodes": [
            {"id": A, "type": "builtin:trigger", "name": "Start", "config": {}},
            {"id": B, "type": "worker:http_request", "name": "Call", "config": {"url": "https://old"}},
            {"id": C, "type": "builtin:output", "name": "Done", "config": {}},
        ],
        "edges": [{"source": A, "target": B}, {"source": B, "target": C}],
    }
    cp = InMemoryControlPlane({"wf-7": base})
    catalog = FakeCatalog([
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["http_in"], out_ports=["success"]),
        CatalogNode(id="builtin:output", name="O", node_type="OUTPUT",
                    in_ports=["output_in"], out_ports=["out_default"]),
    ])

    # The model: load the workflow, then change ONLY node b's url (no re-emit of a/c, no edges).
    script = [
        FakeAI("Loading the workflow.", [_call("get_workflow", {"workflow_id": "wf-7"}, "1")]),
        FakeAI("Changing the URL.", [_call("update_workflow", {
            "workflow_id": "wf-7", "name": "greet",
            "nodes": [{"id": B, "type": "worker:http_request", "name": "Call",
                       "config": {"url": "https://new"}}],
            "edges": [],
        }, "2")]),
        FakeAI("Updated the URL for you."),
    ]

    tools = build_local_tools(catalog, cp)
    designer = ToolLoopWorkflowDesigner(FakeLLM(ScriptedClient(script)), tools)
    gate = ValidateRepairGate(cp, repairer=_NoRepair())
    use_case = DesignWorkflowUseCase(designer, gate)

    result = await use_case.design("change the URL to https://new",
                                   current_workflow={"workflow_id": "wf-7", "workflow": {}})

    assert result.status.lower() == "success"
    assert result.workflow_id == "wf-7"                 # id stayed stable
    assert len(cp.patched) == 1 and not cp.created       # persisted via PATCH, not create/PUT
    # The gate hands the client the merged graph in agent shape (the client enriches
    # worker_type/icon + maps to control-plane shape — covered by the client's own tests).
    persisted = cp.patched[0][1]
    by_id = {n["id"]: n for n in persisted["nodes"]}
    assert set(by_id) == {A, B, C}                       # unrelated a, c preserved (AC B1)
    assert by_id[B]["type"] == "worker:http_request"
    assert by_id[B]["config"]["url"] == "https://new"    # the change applied
    # untouched wiring survived without the model re-sending it (AC B2)
    pairs = {(e["source"], e["target"]) for e in persisted["edges"]}
    assert (A, B) in pairs and (B, C) in pairs


class _NoRepair:
    async def repair(self, definition, error_message, error_details):
        return definition
