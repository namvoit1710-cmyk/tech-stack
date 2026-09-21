from __future__ import annotations

from app.layer1_domain.entities.workflow_run import RunOutcome, WorkflowRef
from app.layer4_frameworks.tools.authoring_tools import build_authoring_tools

NODES = [
    {"id": "trigger", "type": "TRIGGER", "name": "Start", "config": {}},
    {"id": "prompt", "type": "PROMPT", "name": "Draft", "config": {"content": "hi"}},
    {"id": "out", "type": "OUTPUT", "name": "Done", "config": {}},
]
EDGES = [
    {"source": "trigger", "target": "prompt"},
    {"source": "prompt", "target": "out"},
]


class FakeControlPlane:
    def __init__(self, outcome=None, create_error=None, update_error=None, run_error=None,
                 loaded=None, get_error=None, listed=None) -> None:
        self._outcome = outcome or RunOutcome(status="COMPLETED", run_id="run-1", replay_link="/replay/run-1")
        self._create_error = create_error
        self._update_error = update_error
        self._run_error = run_error
        self._loaded = loaded
        self._get_error = get_error
        self._listed = listed or []
        self.created_document: dict | None = None
        self.updated: tuple[str, dict] | None = None
        self.ran: str | None = None
        self.got: str | None = None

    async def list(self) -> list[dict]:
        return list(self._listed)

    async def get(self, workflow_id: str) -> dict:
        if self._get_error:
            raise self._get_error
        self.got = workflow_id
        return self._loaded or {"name": "wf", "nodes": [], "edges": []}

    async def patch(self, workflow_id: str, definition: dict) -> WorkflowRef:
        return WorkflowRef(workflow_id=workflow_id, link=f"/workflow/{workflow_id}")

    async def create(self, definition: dict) -> WorkflowRef:
        if self._create_error:
            raise self._create_error
        self.created_document = definition
        return WorkflowRef(workflow_id="wf-1", link="/workflow/wf-1")

    async def update(self, workflow_id: str, definition: dict) -> WorkflowRef:
        if self._update_error:
            raise self._update_error
        self.updated = (workflow_id, definition)
        return WorkflowRef(workflow_id=workflow_id, link=f"/workflow/{workflow_id}")

    async def test_run(self, workflow_id: str, input_data: dict) -> RunOutcome:
        if self._run_error:
            raise self._run_error
        self.ran = workflow_id
        return self._outcome


def _tools(cp):
    return {t.name: t for t in build_authoring_tools(cp)}


async def test_validate_workflow_accepts_valid_graph() -> None:
    result = await _tools(FakeControlPlane())["validate_workflow"].ainvoke(
        {"name": "wf", "nodes": NODES, "edges": EDGES})
    assert result == {"valid": True, "error": ""}


async def test_validate_workflow_rejects_single_node() -> None:
    result = await _tools(FakeControlPlane())["validate_workflow"].ainvoke(
        {"name": "wf", "nodes": [NODES[0]], "edges": []})
    assert result["valid"] is False and "at least" in result["error"]


async def test_create_workflow_only_validates_and_lays_out_no_persist() -> None:
    cp = FakeControlPlane()
    result = await _tools(cp)["create_workflow"].ainvoke(
        {"name": "wf", "description": "d", "nodes": NODES, "edges": EDGES})
    assert result["status"] == "ready"
    assert "workflow_id" not in result
    assert cp.created_document is None  # persistence now owned by the ValidateRepairGate
    assert all("x" in n and "y" in n for n in result["definition"]["nodes"])  # auto-layout


async def test_create_workflow_reports_invalid_without_persisting() -> None:
    cp = FakeControlPlane()
    result = await _tools(cp)["create_workflow"].ainvoke(
        {"name": "wf", "nodes": [NODES[0]], "edges": []})
    assert result["status"] == "invalid" and cp.created_document is None


async def test_update_workflow_carries_the_id_without_persisting() -> None:
    cp = FakeControlPlane()
    result = await _tools(cp)["update_workflow"].ainvoke(
        {"workflow_id": "wf-1", "name": "wf v2", "nodes": NODES, "edges": EDGES})
    assert result["status"] == "ready"
    assert result["workflow_id"] == "wf-1"
    assert result["remove_node_ids"] == []
    assert cp.updated is None  # persistence now owned by the ValidateRepairGate


async def test_get_workflow_loads_the_definition() -> None:
    loaded = {"name": "wf", "nodes": [{"id": "a", "type": "TASK", "name": "A", "config": {}}], "edges": []}
    cp = FakeControlPlane(loaded=loaded)
    result = await _tools(cp)["get_workflow"].ainvoke({"workflow_id": "wf-9"})
    assert result["status"] == "loaded"
    assert result["workflow_id"] == "wf-9"
    assert result["definition"] == loaded
    assert cp.got == "wf-9"


async def test_get_workflow_requires_an_id() -> None:
    result = await _tools(FakeControlPlane())["get_workflow"].ainvoke({"workflow_id": ""})
    assert result["status"] == "error"


async def test_get_workflow_routes_a_name_to_find_workflows_without_calling_the_control_plane() -> None:
    # SA-2131 deployed bug: the model passed the NAME as the id; get_workflow must not error out
    # but steer to find_workflows (and never hit the control plane with a non-id).
    cp = FakeControlPlane()
    result = await _tools(cp)["get_workflow"].ainvoke({"workflow_id": "Get Weather Data for Vietnam"})
    assert result["status"] == "needs_lookup"
    assert "find_workflows" in result["message"]
    assert cp.got is None  # never asked the control plane for a name


async def test_get_workflow_error_steers_to_find_workflows() -> None:
    from app.layer1_domain.interfaces.workflow_control_plane_interface import (
        WorkflowControlPlaneError,
    )
    cp = FakeControlPlane(get_error=WorkflowControlPlaneError("NOT_FOUND", "No such workflow."))
    result = await _tools(cp)["get_workflow"].ainvoke({"workflow_id": "wf-missing"})
    assert result["status"] == "error"
    assert "find_workflows" in result["message"]


async def test_update_workflow_accepts_a_partial_diff_and_removals() -> None:
    # A diff may be a single changed node (not a complete >=2/connected graph) — the tool
    # narrows shapes only; whole-graph rules run on the merged graph in the gate.
    cp = FakeControlPlane()
    result = await _tools(cp)["update_workflow"].ainvoke({
        "workflow_id": "wf-1", "name": "wf",
        "nodes": [{"id": "b", "type": "TASK", "name": "B2", "config": {"x": 2}}],
        "edges": [], "remove_node_ids": ["old", ""],
    })
    assert result["status"] == "ready"
    assert result["remove_node_ids"] == ["old"]                       # blanks dropped
    assert [n["id"] for n in result["definition"]["nodes"]] == ["b"]   # single node accepted


async def test_update_workflow_requires_id() -> None:
    result = await _tools(FakeControlPlane())["update_workflow"].ainvoke(
        {"workflow_id": "", "name": "wf", "nodes": NODES, "edges": EDGES})
    assert result["status"] == "error"


async def test_clone_workflow_carries_the_source_id_and_partial_diff() -> None:
    # SA-2131: clone hands over a source id + a partial diff (like update) — no persist here.
    cp = FakeControlPlane()
    result = await _tools(cp)["clone_workflow"].ainvoke({
        "source_workflow_id": "wf-src", "name": "copy",
        "nodes": [{"id": "b", "type": "TASK", "name": "B2", "config": {"x": 2}}],
        "edges": [], "remove_node_ids": ["old", ""],
    })
    assert result["status"] == "ready"
    assert result["source_workflow_id"] == "wf-src"
    assert result["remove_node_ids"] == ["old"]                       # blanks dropped
    assert [n["id"] for n in result["definition"]["nodes"]] == ["b"]   # single node accepted
    assert cp.created_document is None and cp.updated is None          # gate owns persistence


async def test_clone_workflow_requires_a_source_id() -> None:
    result = await _tools(FakeControlPlane())["clone_workflow"].ainvoke(
        {"source_workflow_id": "", "name": "copy", "nodes": NODES, "edges": EDGES})
    assert result["status"] == "error"


_WORKFLOWS = [
    {"id": "wf-a", "name": "Get Weather Data for Vietnam", "description": "current weather"},
    {"id": "wf-b", "name": "Daily Sales Report", "description": "email a report"},
    {"id": "wf-c", "name": "Weather alerts", "description": "push alerts"},
]


async def test_find_workflows_matches_exact_name_case_insensitively() -> None:
    cp = FakeControlPlane(listed=_WORKFLOWS)
    result = await _tools(cp)["find_workflows"].ainvoke({"query": "get weather data for vietnam"})
    assert result["status"] == "ok"
    assert result["matches"][0] == {
        "workflow_id": "wf-a", "name": "Get Weather Data for Vietnam",
        "description": "current weather", "match": "exact",
    }


async def test_find_workflows_ranks_a_typo_as_a_close_match() -> None:
    # SA-2131 issue: the user typed the name slightly wrong; still resolve it (to confirm).
    cp = FakeControlPlane(listed=_WORKFLOWS)
    result = await _tools(cp)["find_workflows"].ainvoke({"query": "Get Weather Data for Vietnm"})
    assert result["status"] == "ok"
    top = result["matches"][0]
    assert top["workflow_id"] == "wf-a"
    assert top["match"] in ("contains", "close")  # typo still surfaces the right workflow


async def test_find_workflows_returns_no_matches_for_an_unrelated_query() -> None:
    cp = FakeControlPlane(listed=_WORKFLOWS)
    result = await _tools(cp)["find_workflows"].ainvoke({"query": "invoice reconciliation"})
    assert result["status"] == "ok"
    assert result["matches"] == []


async def test_find_workflows_requires_a_query() -> None:
    result = await _tools(FakeControlPlane(listed=_WORKFLOWS))["find_workflows"].ainvoke({"query": " "})
    assert result["status"] == "error"


async def test_run_workflow_reports_status() -> None:
    cp = FakeControlPlane()
    result = await _tools(cp)["run_workflow"].ainvoke({"workflow_id": "wf-1"})
    assert result["status"] == "run"
    assert result["run_status"] == "COMPLETED"
    assert cp.ran == "wf-1"


async def test_run_workflow_surfaces_run_error() -> None:
    cp = FakeControlPlane(outcome=RunOutcome(status="FAILED", run_id="r", error="node blew up"))
    result = await _tools(cp)["run_workflow"].ainvoke({"workflow_id": "wf-1"})
    assert result["status"] == "run"
    assert result["run_error"] == "node blew up"
