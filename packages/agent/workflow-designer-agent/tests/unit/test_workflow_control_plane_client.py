from __future__ import annotations

import pytest

from app.layer1_domain.entities.catalog import CatalogNode
from app.layer1_domain.interfaces.workflow_control_plane_interface import (
    WorkflowControlPlaneError,
)
from app.layer4_frameworks.clients.workflow_control_plane_client import (
    HttpWorkflowControlPlane,
    _is_uuid,
)


class _RecordingClient:
    """Minimal httpx.AsyncClient stand-in that records the request and returns a scripted body."""

    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self.body = body
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, json: dict):
        self.calls.append((method, url, json))
        return _FakeResponse(self.status_code, self.body)

    async def aclose(self) -> None:
        pass


class _FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


DEFINITION = {
    "name": "CSV flow",
    "description": "ingest and validate",
    "nodes": [
        {"id": "trigger", "type": "trigger", "name": "Upload", "config": {"input_binding": {"file_id": "x"}}},
        {"id": "task", "type": "task", "name": "Validate", "config": {}},
        {"id": "out", "type": "output", "name": "Done", "config": {}},
    ],
    "edges": [
        {"source": "trigger", "target": "task"},
        {"source": "task", "target": "out"},
    ],
}


class FakeCatalog:
    """Serves the ports the client resolves edges from (mirrors the live catalog)."""

    def __init__(self, nodes) -> None:
        self._nodes = nodes

    async def nodes(self):
        return self._nodes

    async def node_schemas(self, refs):
        return []

    async def workers(self):
        return []


def _catalog():
    return FakeCatalog([
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
        CatalogNode(id="builtin:task", name="K", node_type="TASK",
                    in_ports=["task_in"], out_ports=["success"]),
        CatalogNode(id="builtin:output", name="O", node_type="OUTPUT",
                    in_ports=["output_in"], out_ports=["out_default"]),
    ])


async def test_to_create_payload_uses_catalog_ports() -> None:
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=_catalog())
    payload = await cp._to_create_payload(DEFINITION)
    assert payload["name"] == "CSV flow"
    node = payload["nodes"][0]
    assert node["node_type"] == "TRIGGER"
    # config is sent as the unified `parameters` field (control plane spec-splits it)
    assert "parameters" in node and "config" not in node
    e1, e2 = payload["edges"]
    # ports come from the live catalog, not a hardcoded per-type table
    assert e1["source_port_id"] == "trigger_out"
    assert e1["target_port_id"] == "task_in"
    assert e2["source_port_id"] == "success"
    assert e2["target_port_id"] == "output_in"


async def test_to_create_payload_falls_back_when_catalog_empty() -> None:
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=FakeCatalog([]))
    payload = await cp._to_create_payload(DEFINITION)
    e1 = payload["edges"][0]
    assert e1["source_port_id"] == "out_default"
    assert e1["target_port_id"] == "in_default"


async def test_task_worker_type_is_lifted_and_drives_port_lookup() -> None:
    definition = {
        "name": "wf", "description": "",
        "nodes": [
            {"id": "t", "type": "TRIGGER", "name": "T", "config": {}},
            {"id": "w", "type": "TASK", "name": "Work", "config": {"worker_type": "worker:http_request"}},
        ],
        "edges": [{"source": "t", "target": "w"}],
    }
    catalog = FakeCatalog([
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["http_in"], out_ports=["success"]),
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
    ])
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=catalog)
    payload = await cp._to_create_payload(definition)
    assert payload["nodes"][1]["worker_type"] == "worker:http_request"
    # target port resolved via the worker id, not the generic TASK
    assert payload["edges"][0]["target_port_id"] == "http_in"


async def test_node_type_normalized_from_catalog_id() -> None:
    # A model that puts the catalog id as the node "type" (a common mistake) still persists
    # as the real node_type + worker_type — driven by the live catalog, not hardcoded.
    definition = {
        "name": "wf", "description": "",
        "nodes": [
            {"id": "t", "type": "TRIGGER", "name": "T", "config": {}},
            {"id": "h", "type": "worker:http_request", "name": "HTTP",
             "config": {"method": "GET", "url": "https://x"}},
        ],
        "edges": [{"source": "t", "target": "h"}],
    }
    catalog = FakeCatalog([
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["http_in"], out_ports=["success"]),
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
    ])
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=catalog)
    payload = await cp._to_create_payload(definition)
    http = payload["nodes"][1]
    assert http["node_type"] == "TASK"                       # resolved from the id
    assert http["worker_type"] == "worker:http_request"      # id lifted to worker_type
    assert http["parameters"] == {"method": "GET", "url": "https://x"}


async def test_worker_node_is_enriched_with_worker_type_icon_and_color() -> None:
    """SA-2163 fix: a node identified by its catalog id is enriched from the catalog like the
    FE palette drop — worker_type = the id, plus icon/color — so it is bound and displays right
    (the live bug was worker_type: null, icon: "" on agent-created nodes). Builtins get icon/
    color but no bogus worker_type (the control plane sets builtin:* itself)."""
    definition = {
        "name": "wf", "description": "",
        "nodes": [
            {"id": "h", "type": "worker:http_request", "name": "HTTP",
             "config": {"method": "GET", "url": "https://google.com"}},
            {"id": "o", "type": "builtin:output", "name": "Done", "config": {}},
        ],
        "edges": [{"source": "h", "target": "o"}],
    }
    catalog = FakeCatalog([
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    icon="Cog", color="#3B82F6", in_ports=["in_default"], out_ports=["success"]),
        CatalogNode(id="builtin:output", name="Output", node_type="OUTPUT",
                    icon="LogOut", color="#10B981", in_ports=["output_in"], out_ports=["out_default"]),
    ])
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=catalog)
    payload = await cp._to_create_payload(definition)
    http, out = payload["nodes"]
    assert http["node_type"] == "TASK"
    assert http["worker_type"] == "worker:http_request"   # the fix: bound, not null
    assert http["icon"] == "Cog" and http["color"] == "#3B82F6"
    assert out["node_type"] == "OUTPUT"
    assert "worker_type" not in out                        # builtin: control plane fills it
    assert out["icon"] == "LogOut" and out["color"] == "#10B981"


async def test_business_node_carries_node_definition_id() -> None:
    """A business:<def-id> node is enriched with node_definition_id + worker_type so the control
    plane can normalize it to the runnable base worker (matches the FE mapNodeToEditorNode)."""
    definition = {
        "name": "wf", "description": "",
        "nodes": [
            {"id": "t", "type": "builtin:trigger", "name": "T", "config": {}},
            {"id": "b", "type": "business:abc123", "name": "Custom", "config": {}},
        ],
        "edges": [{"source": "t", "target": "b"}],
    }
    catalog = FakeCatalog([
        CatalogNode(id="business:abc123", name="Custom", node_type="TASK",
                    in_ports=["in_default"], out_ports=["success"]),
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
    ])
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=catalog)
    payload = await cp._to_create_payload(definition)
    biz = payload["nodes"][1]
    assert biz["worker_type"] == "business:abc123"
    assert biz["node_definition_id"] == "abc123"


async def test_node_ids_become_uuids_and_edges_and_bindings_follow() -> None:
    """SA-2163: node ids are rewritten to UUIDs (like palette-created nodes); edges and binding
    expressions ($<node_id>) are remapped to the new ids so the graph stays wired."""
    definition = {
        "name": "wf", "description": "",
        "nodes": [
            {"id": "trigger", "type": "builtin:trigger", "name": "T", "config": {}},
            {"id": "http_1", "type": "worker:http_request", "name": "HTTP",
             "config": {"url": "https://x", "body": "{{$trigger.output.file_id}}"}},
        ],
        "edges": [{"source": "trigger", "target": "http_1"}],
    }
    catalog = FakeCatalog([
        CatalogNode(id="builtin:trigger", name="T", node_type="TRIGGER",
                    in_ports=["in_default"], out_ports=["trigger_out"]),
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["http_in"], out_ports=["success"]),
    ])
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=catalog)
    payload = await cp._to_create_payload(definition)

    trig_id, http_id = (n["id"] for n in payload["nodes"])
    assert _is_uuid(trig_id) and _is_uuid(http_id)          # both handles -> UUIDs
    assert payload["edges"][0]["source_node_id"] == trig_id  # edge follows the rename
    assert payload["edges"][0]["target_node_id"] == http_id
    # the binding was rewritten to the trigger's new UUID (output field left intact)
    assert payload["nodes"][1]["parameters"]["body"] == f"{{{{${trig_id}.output.file_id}}}}"


async def test_existing_uuid_node_id_is_preserved() -> None:
    """A node id that is already a UUID is kept as-is, so re-saving a workflow is stable."""
    uid = "818b803e-62ef-4bdd-8b0a-b3ddcb364014"
    definition = {
        "name": "wf", "description": "",
        "nodes": [
            {"id": uid, "type": "worker:http_request", "name": "HTTP", "config": {"url": "https://x"}},
            {"id": "o", "type": "builtin:output", "name": "Done", "config": {}},
        ],
        "edges": [{"source": uid, "target": "o"}],
    }
    catalog = FakeCatalog([
        CatalogNode(id="worker:http_request", name="HTTP", node_type="TASK",
                    in_ports=["in_default"], out_ports=["success"]),
        CatalogNode(id="builtin:output", name="O", node_type="OUTPUT",
                    in_ports=["output_in"], out_ports=["out_default"]),
    ])
    cp = HttpWorkflowControlPlane(base_url="http://cp", catalog=catalog)
    payload = await cp._to_create_payload(definition)

    assert payload["nodes"][0]["id"] == uid          # already a UUID -> preserved
    assert _is_uuid(payload["nodes"][1]["id"])        # "o" -> a fresh UUID
    assert payload["edges"][0]["source_node_id"] == uid


async def test_get_loads_and_maps_stored_doc_back_to_agent_shape() -> None:
    body = {"status": "ok", "data": {
        "name": "wf", "description": "d",
        "nodes": [
            {"id": "n1", "node_type": "TASK", "worker_type": "worker:http_request",
             "parameters": {"url": "https://x"}, "x": 10, "y": 20},
            {"id": "n2", "node_type": "OUTPUT", "parameters": {}},
            {"id": "n3", "node_type": "TASK", "node_definition_id": "abc123", "parameters": {}},
        ],
        "edges": [{"source_node_id": "n1", "target_node_id": "n2"}],
    }}
    client = _RecordingClient(200, body)
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=client, catalog=_catalog())

    definition = await cp.get("wf-1")

    assert client.calls[0][:2] == ("GET", "http://cp/api/v1/workflows/wf-1")
    n1, n2, n3 = definition["nodes"]
    assert n1["type"] == "worker:http_request"      # worker_type -> catalog id
    assert n1["config"] == {"url": "https://x"} and n1["x"] == 10 and n1["y"] == 20
    assert n2["type"] == "builtin:output"           # builtin resolved from node_type via catalog
    assert n3["type"] == "business:abc123"          # node_definition_id -> business id
    assert definition["edges"] == [{"source": "n1", "target": "n2"}]


async def test_patch_sends_the_payload_and_keeps_the_id_stable() -> None:
    client = _RecordingClient(200, {"status": "ok", "data": {"id": "wf-1"}})
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=client, catalog=_catalog())

    ref = await cp.patch("wf-1", DEFINITION)

    method, url, payload = client.calls[0]
    assert method == "PATCH" and url == "http://cp/api/v1/workflows/wf-1"
    assert payload["nodes"] and payload["edges"]     # full merged graph sent
    assert ref.workflow_id == "wf-1"                 # PATCH does not fork the id


async def test_list_returns_id_name_description_summaries() -> None:
    body = {"status": "ok", "data": {"items": [
        {"id": "wf-a", "name": "Get Weather Data for Vietnam", "description": "current weather",
         "nodes": [{"id": "x"}]},   # extra fields (nodes/edges) are dropped from the summary
        {"id": "wf-b", "name": "Sales Report"},
        {"name": "no id — skipped"},
    ], "total": 3}}
    client = _RecordingClient(200, body)
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=client)

    summaries = await cp.list()

    method, url, _ = client.calls[0]
    assert method == "GET" and url == "http://cp/api/v1/workflows"
    assert summaries == [
        {"id": "wf-a", "name": "Get Weather Data for Vietnam", "description": "current weather"},
        {"id": "wf-b", "name": "Sales Report", "description": ""},
    ]


def test_unwrap_surfaces_reason_detail() -> None:
    with pytest.raises(WorkflowControlPlaneError) as exc:
        HttpWorkflowControlPlane._unwrap(400, {"status": "error", "error": {
            "code": "INVALID_NODE_DATA", "message": "Invalid node data",
            "details": {"reason": "'worker:http_request' is not a valid NodeType"}}})
    assert "not a valid NodeType" in exc.value.message


def test_unwrap_returns_data_on_ok() -> None:
    data = HttpWorkflowControlPlane._unwrap(200, {"status": "ok", "data": {"id": "wf-1"}})
    assert data == {"id": "wf-1"}


def test_unwrap_raises_on_error_envelope() -> None:
    with pytest.raises(WorkflowControlPlaneError) as exc:
        HttpWorkflowControlPlane._unwrap(400, {"status": "error", "error": {"code": "NODE_CONFIG_INVALID", "message": "bad"}})
    assert exc.value.code == "NODE_CONFIG_INVALID"


async def test_post_raises_clear_error_when_base_url_has_no_protocol() -> None:
    cp = HttpWorkflowControlPlane(base_url="default_workflow_api_base_url")
    with pytest.raises(WorkflowControlPlaneError) as exc:
        await cp._post("/workflows", {})
    assert exc.value.code == "CONTROL_PLANE_NOT_CONFIGURED"


async def test_dry_run_reports_ok_true_on_valid_graph() -> None:
    client = _RecordingClient(200, {"status": "ok", "data": {"workflow_id": "wf-1", "is_valid": True}})
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=client)
    outcome = await cp.dry_run("wf-1")
    assert outcome.ok is True
    assert client.calls[0][:2] == ("POST", "http://cp/api/v1/workflows/wf-1/dry-run")


async def test_dry_run_reports_error_code_and_message_on_failure() -> None:
    body = {"status": "ok", "data": {
        "is_valid": False, "error_code": "INVALID_EDGE_REFERENCE", "error": "unknown edge target",
        "error_details": {"edge": "e1"},
    }}
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=_RecordingClient(200, body))
    outcome = await cp.dry_run("wf-1")
    assert outcome.ok is False
    assert outcome.error_code == "INVALID_EDGE_REFERENCE"
    assert outcome.error_message == "unknown edge target"
    assert outcome.error_details == {"edge": "e1"}


async def test_dry_run_falls_back_to_validation_errors_when_no_error_message() -> None:
    body = {"status": "ok", "data": {"is_valid": False, "validation_errors": ["bad graph"]}}
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=_RecordingClient(200, body))
    outcome = await cp.dry_run("wf-1")
    assert outcome.ok is False
    assert "bad graph" in outcome.error_message


async def test_test_run_defaults_skip_validation_true() -> None:
    client = _RecordingClient(200, {"status": "ok", "data": {"status": "COMPLETED", "run_id": "r-1"}})
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=client)
    await cp.test_run("wf-1", {})
    assert client.calls[0][2]["skip_validation"] is True


async def test_test_run_honors_skip_validation_false() -> None:
    client = _RecordingClient(200, {"status": "ok", "data": {"status": "COMPLETED", "run_id": "r-1"}})
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=client)
    await cp.test_run("wf-1", {}, skip_validation=False)
    assert client.calls[0][2]["skip_validation"] is False


async def test_test_run_surfaces_error_code_on_the_outcome() -> None:
    body = {"status": "ok", "data": {
        "status": "FAILED", "run_id": "r-1", "error": "boom", "error_code": "INVALID_NODE_DATA",
    }}
    cp = HttpWorkflowControlPlane(base_url="http://cp", client=_RecordingClient(200, body))
    outcome = await cp.test_run("wf-1", {}, skip_validation=False)
    assert outcome.failed() is True
    assert outcome.error_code == "INVALID_NODE_DATA"
