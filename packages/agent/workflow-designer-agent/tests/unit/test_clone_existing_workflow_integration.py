"""SA-2131 end-to-end (Overall AC): clone an existing workflow by id via NL, driving the real
designer + use case + gate, so load(source) -> diff -> validate/dry-run -> create-persist works
together and the SOURCE is left unchanged.

A scripted LLM plays the model; the control plane is an in-memory fake that stores workflows, so
the assertions are made against what actually gets persisted (a NEW workflow, source untouched)."""
from __future__ import annotations

import copy

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
    """Stores workflows in-process so get() returns the source and create() persists the clone."""

    def __init__(self, seed: dict) -> None:
        self.store = {k: copy.deepcopy(v) for k, v in seed.items()}
        self.patched: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, dict]] = []
        self.created: list[dict] = []
        self._new_id = 0

    async def get(self, workflow_id: str) -> dict:
        return copy.deepcopy(self.store[workflow_id])

    async def list(self) -> list[dict]:
        return [{"id": wid, "name": wf.get("name", ""), "description": wf.get("description", "")}
                for wid, wf in self.store.items()]

    async def create(self, definition: dict) -> WorkflowRef:
        self.created.append(definition)
        self._new_id += 1
        wid = f"wf-clone-{self._new_id}"
        self.store[wid] = definition
        return WorkflowRef(workflow_id=wid, link=f"/workflow/{wid}")

    async def update(self, workflow_id: str, definition: dict) -> WorkflowRef:
        self.updated.append((workflow_id, definition))
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


class _NoRepair:
    async def repair(self, definition, error_message, error_details):
        return definition


def _call(name, args, cid):
    return {"name": name, "args": args, "id": cid}


A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"


async def test_clone_existing_workflow_by_id_end_to_end() -> None:
    # A stored source workflow whose nodes carry UUID ids (as the control plane stores them).
    source = {
        "name": "greet", "description": "d",
        "nodes": [
            {"id": A, "type": "builtin:trigger", "name": "Start", "config": {}},
            {"id": B, "type": "worker:http_request", "name": "Call", "config": {"url": "https://old"}},
            {"id": C, "type": "builtin:output", "name": "Done", "config": {}},
        ],
        "edges": [{"source": A, "target": B}, {"source": B, "target": C}],
    }
    cp = InMemoryControlPlane({"wf-src": source})
    source_before = copy.deepcopy(cp.store["wf-src"])
    catalog = FakeCatalog([
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["http_in"], out_ports=["success"]),
        CatalogNode(id="builtin:output", name="O", node_type="OUTPUT",
                    in_ports=["output_in"], out_ports=["out_default"]),
    ])

    # The model: load the source, then clone it changing ONLY node b's url (no re-emit of a/c).
    script = [
        FakeAI("Loading the source workflow.", [_call("get_workflow", {"workflow_id": "wf-src"}, "1")]),
        FakeAI("Cloning with a new URL.", [_call("clone_workflow", {
            "source_workflow_id": "wf-src", "name": "greet (copy)",
            "nodes": [{"id": B, "type": "worker:http_request", "name": "Call",
                       "config": {"url": "https://new"}}],
            "edges": [],
        }, "2")]),
        FakeAI("Created a copy for you."),
    ]

    tools = build_local_tools(catalog, cp)
    designer = ToolLoopWorkflowDesigner(FakeLLM(ScriptedClient(script)), tools)
    gate = ValidateRepairGate(cp, repairer=_NoRepair())
    use_case = DesignWorkflowUseCase(designer, gate)

    result = await use_case.design("make a copy of this workflow but call https://new",
                                   current_workflow={"source_workflow_id": "wf-src", "workflow": {}})

    assert result.status.lower() == "success"
    assert result.workflow_id == "wf-clone-1"            # a NEW workflow id
    assert result.workflow_id != "wf-src"
    assert len(cp.created) == 1 and not cp.patched and not cp.updated  # persisted via create() only
    # AC: the source workflow is unchanged (never written — only read).
    assert cp.store["wf-src"] == source_before
    # The clone carries the source graph with the requested change applied.
    persisted = cp.created[0]
    by_id = {n["id"]: n for n in persisted["nodes"]}
    assert set(by_id) == {A, B, C}                       # unrelated a, c copied (source template)
    assert by_id[B]["config"]["url"] == "https://new"    # the change applied in the clone
    pairs = {(e["source"], e["target"]) for e in persisted["edges"]}
    assert (A, B) in pairs and (B, C) in pairs           # untouched wiring copied


async def test_clone_by_name_resolves_the_source_id_then_clones() -> None:
    # The user names the source workflow instead of passing an id (the SA-2131 bug report):
    # the agent must find_workflows -> get_workflow -> clone_workflow, and still leave the
    # source untouched. No source_workflow_id is seeded from the request.
    source = {
        "name": "Get Weather Data for Vietnam", "description": "d",
        "nodes": [
            {"id": A, "type": "builtin:trigger", "name": "Start", "config": {}},
            {"id": B, "type": "worker:http_request", "name": "Call",
             "config": {"url": "https://old?city=Hanoi"}},
            {"id": C, "type": "builtin:output", "name": "Done", "config": {}},
        ],
        "edges": [{"source": A, "target": B}, {"source": B, "target": C}],
    }
    cp = InMemoryControlPlane({"wf-src": source})
    source_before = copy.deepcopy(cp.store["wf-src"])
    catalog = FakeCatalog([
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["http_in"], out_ports=["success"]),
        CatalogNode(id="builtin:output", name="O", node_type="OUTPUT",
                    in_ports=["output_in"], out_ports=["out_default"]),
    ])

    # The model resolves the name to wf-src, loads it, then clones with Hai Phong.
    script = [
        FakeAI("Finding the workflow you named.",
               [_call("find_workflows", {"query": "Get Weather Data for Vietnam"}, "1")]),
        FakeAI("Loading the source workflow.", [_call("get_workflow", {"workflow_id": "wf-src"}, "2")]),
        FakeAI("Cloning with Hai Phong.", [_call("clone_workflow", {
            "source_workflow_id": "wf-src", "name": "Get Weather Data for Hai Phong",
            "nodes": [{"id": B, "type": "worker:http_request", "name": "Call",
                       "config": {"url": "https://old?city=HaiPhong"}}],
            "edges": [],
        }, "3")]),
        FakeAI("Created a copy for you."),
    ]

    tools = build_local_tools(catalog, cp)
    designer = ToolLoopWorkflowDesigner(FakeLLM(ScriptedClient(script)), tools)
    gate = ValidateRepairGate(cp, repairer=_NoRepair())
    use_case = DesignWorkflowUseCase(designer, gate)

    # No source_workflow_id seeded — the agent must resolve it from the name itself.
    result = await use_case.design(
        'Clone a workflow "Get Weather Data for Vietnam" but change Hanoi to Hai Phong',
        current_workflow=None)

    assert result.status.lower() == "success"
    assert result.workflow_id == "wf-clone-1" and result.workflow_id != "wf-src"
    assert len(cp.created) == 1 and not cp.patched and not cp.updated
    assert cp.store["wf-src"] == source_before                    # source untouched
    persisted = cp.created[0]
    by_id = {n["id"]: n for n in persisted["nodes"]}
    assert by_id[B]["config"]["url"] == "https://old?city=HaiPhong"  # change applied
    assert set(by_id) == {A, B, C}                                # rest copied from source


async def test_clone_recovers_when_the_model_passes_the_name_as_the_id() -> None:
    # Reproduces the deployed SA-2131 bug: the model calls get_workflow with the NAME first.
    # get_workflow must steer to find_workflows (needs_lookup) so the loop recovers instead of
    # dying with "I couldn't design a workflow".
    source = {
        "name": "Get Weather Data for Vietnam", "description": "d",
        "nodes": [
            {"id": A, "type": "builtin:trigger", "name": "Start", "config": {}},
            {"id": B, "type": "worker:http_request", "name": "Call",
             "config": {"url": "https://old?city=Hanoi"}},
            {"id": C, "type": "builtin:output", "name": "Done", "config": {}},
        ],
        "edges": [{"source": A, "target": B}, {"source": B, "target": C}],
    }
    cp = InMemoryControlPlane({"wf-src": source})
    catalog = FakeCatalog([
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["http_in"], out_ports=["success"]),
        CatalogNode(id="builtin:output", name="O", node_type="OUTPUT",
                    in_ports=["output_in"], out_ports=["out_default"]),
    ])

    script = [
        # The model's mistake: pass the NAME as the id.
        FakeAI("Loading it.", [_call("get_workflow", {"workflow_id": "Get Weather Data for Vietnam"}, "1")]),
        # It's steered to resolve by name, then loads by the real id.
        FakeAI("Resolving the name.",
               [_call("find_workflows", {"query": "Get Weather Data for Vietnam"}, "2")]),
        FakeAI("Loading the real id.", [_call("get_workflow", {"workflow_id": "wf-src"}, "3")]),
        FakeAI("Cloning.", [_call("clone_workflow", {
            "source_workflow_id": "wf-src", "name": "Get Weather Data for Hai Phong",
            "nodes": [{"id": B, "type": "worker:http_request", "name": "Call",
                       "config": {"url": "https://old?city=HaiPhong"}}],
            "edges": [],
        }, "4")]),
        FakeAI("Created a copy for you."),
    ]

    tools = build_local_tools(catalog, cp)
    designer = ToolLoopWorkflowDesigner(FakeLLM(ScriptedClient(script)), tools)
    gate = ValidateRepairGate(cp, repairer=_NoRepair())
    use_case = DesignWorkflowUseCase(designer, gate)

    result = await use_case.design(
        'Clone a workflow "Get Weather Data for Vietnam" but change Hanoi to Hai Phong',
        current_workflow=None)

    assert result.status.lower() == "success"
    assert result.workflow_id == "wf-clone-1"
    assert len(cp.created) == 1 and cp.created[0]["nodes"]  # recovered and persisted a clone
