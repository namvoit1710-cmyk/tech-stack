"""FE `parameters` round-trip through the real HTTP save/read paths
(integration matrix Module 2).

The spec-driven split runs server-side on save; every read surface
(GET /workflows/{id}, node-tree, publish) must hand the FE back the
merged flat ``parameters`` with no engine internals leaking. R2 is the
wiring net for ALL catalog node types: each representative parameters
key must land in ``config`` vs ``input_binding`` exactly as the spec
declares.
"""

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _post(client, nodes, name):
    return assert_ok(client.post(
        "/api/v1/workflows", json={"name": name, "nodes": nodes},
    ))


# Representative FE-form parameters per catalog node type. Each entry
# includes at least one declared config key and (where sensible) one
# free-form binding key, so the split assertion is non-trivial.
_REPRESENTATIVE_PARAMS: dict[str, dict] = {
    "CONDITION": {"data": [{"key": "{{$input.n}}", "operations": ">", "value": 1}],
                  "extra_binding": "{{$a.b}}"},
    "SWITCH": {"data": [{"key": "{{$input.x}}", "operations": "==",
                         "value": "1", "branch": "b1"}]},
    "LOOP": {"loop_type": "collection", "collection": "{{$input.items}}",
             "max_iterations": "5", "mode": "sequential"},
    "PARALLEL": {"free_binding": 1},  # form is empty post-redesign
    "MERGE": {"merge_strategy": "n_of_m", "wait_count": "2",
              "timeout_seconds": "60"},
    "TRIGGER": {"trigger_type": "webhook", "webhook_path": "/h/x",
                "event_filter": "$.p == 1"},
    "WORKFLOW": {"workflow_id": "child-1", "timeout_ms": "5000",
                 "input_mapping": [{"param_name": "a", "param_value": "{{$n.out}}"}]},
    "COMPUTE": {"expression": "1 + 1", "result_key": "total"},
    "INPUT": {"instruction": "fill", "runThrough": "true",
              "custom_field": "{{$x.y}}"},
    "HUMAN_ACTION": {"instruction": "approve?", "confirm_label": "Go",
                     "prefill_thing": "{{$x.y}}"},
    "DATA_EDIT": {"file_id": "{{$up.__file_id}}", "query": "$top=5",
                  "readonly": "true"},
    "FILE_UPLOAD": {"instruction": "upload", "artifact_key": "doc",
                    "use_agent": "agent-1"},
    "OUTPUT": {"instruction": "out", "output_schema": [
        {"key": "f", "type": "string", "value": "{{$n.output.f}}"}]},
    "APPROVAL_FLOW": {"change_request_id": "{{$n.output.cr}}"},
    "SET_VARIABLE": {"variables": [
        {"name": "v", "type": "number", "value_expression": "42"}]},
}


# ── R1: POST → GET round-trip, no internals leak ──


def test_parameters_round_trip_and_no_internal_leak(client):
    nodes = [
        {"id": "t1", "name": "T", "node_type": "TRIGGER",
         "parameters": {"trigger_type": "webhook", "webhook_path": "/h/x"}},
        {"id": "m1", "name": "M", "node_type": "MERGE",
         "parameters": {"merge_strategy": "n_of_m", "wait_count": 2}},
        {"id": "c1", "name": "C", "node_type": "CONDITION",
         "parameters": {"data": [
             {"key": "{{$input.n}}", "operations": ">", "value": 1}]}},
    ]
    created = _post(client, nodes, "wf-roundtrip")

    body = assert_ok(client.get(f"/api/v1/workflows/{created['id']}"))
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id["t1"]["parameters"]["webhook_path"] == "/h/x"
    assert by_id["m1"]["parameters"]["merge_strategy"] == "n_of_m"
    assert by_id["m1"]["parameters"]["wait_count"] == 2
    assert by_id["c1"]["parameters"]["data"][0]["operations"] == ">"
    for node in by_id.values():
        assert not any(k.startswith("_") for k in node["parameters"]), (
            "internal keys must never round-trip to the FE"
        )
        assert "definition_snapshot" not in node["parameters"]


# ── R2: split correctness for every catalog node type ──


@pytest.mark.parametrize("node_type", sorted(_REPRESENTATIVE_PARAMS))
def test_split_lands_keys_per_spec(client, container, node_type):
    params = _REPRESENTATIVE_PARAMS[node_type]
    created = _post(client, [
        {"id": "n1", "name": "N", "node_type": node_type,
         "parameters": params},
    ], f"wf-split-{node_type.lower()}")

    stored = container["_infra"].repos.workflow.get_by_id(created["id"])
    node = stored.get_node("n1")
    spec = get_spec(NodeType(node_type))

    for key in params:
        if key in spec.config_keys:
            assert key in node.config, f"{node_type}.{key} should be config"
            assert key not in node.input_binding
        else:
            assert key in node.input_binding, f"{node_type}.{key} should be binding"
            assert key not in node.config

    # store_as fold: WORKFLOW input_mapping rows arrive as the engine dict.
    if node_type == "WORKFLOW":
        assert node.config["input_mapping"] == {"a": "{{$n.out}}"}
        assert node.config["timeout_ms"] == 5000  # coerced number (sweep contract)
    if node_type == "MERGE":
        assert node.config["wait_count"] == 2  # string coerced by spec type
    if node_type == "INPUT":
        assert node.config["runThrough"] is True


# ── R3: node-tree serves merged parameters ──


def test_node_tree_serves_merged_parameters(client):
    created = _post(client, [
        {"id": "t1", "name": "T", "node_type": "TRIGGER",
         "parameters": {"trigger_type": "webhook", "webhook_path": "/h/x"}},
    ], "wf-tree")
    r = client.get(f"/api/v1/workflows/{created['id']}/node-tree")
    body = assert_ok(r)
    nodes = body["nodes"]
    assert nodes, "node-tree returned no nodes"
    t1 = next(n for n in nodes if n["id"] == "t1")
    assert t1["parameters"]["webhook_path"] == "/h/x"
    assert not any(k.startswith("_") for k in t1["parameters"])


# ── R4: publish response serves merged parameters ──


def test_publish_serves_merged_parameters(client):
    created = _post(client, [
        {"id": "m1", "name": "M", "node_type": "MERGE",
         "parameters": {"merge_strategy": "any"}},
    ], "wf-publish")
    r = client.post(
        f"/api/v1/workflows/publish/{created['id']}",
        json={"version": created["version"]},  # publish resolves by (id, version)
    )
    body = assert_ok(r)
    m1 = next(n for n in body["nodes"] if n["id"] == "m1")
    assert m1["parameters"]["merge_strategy"] == "any"
    assert not any(k.startswith("_") for k in m1["parameters"])


# ── R5: explicit-config (API author) payload preserved verbatim ──


def test_explicit_config_bypasses_split(client, container):
    created = _post(client, [
        {"id": "m1", "name": "M", "node_type": "MERGE",
         "config": {"merge_strategy": "all", "defaults": {"x": 1}},
         "input_binding": {"free": "{{$a.b}}"}},
    ], "wf-explicit")
    stored = container["_infra"].repos.workflow.get_by_id(created["id"])
    node = stored.get_node("m1")
    assert node.config == {"merge_strategy": "all", "defaults": {"x": 1}}
    assert node.input_binding == {"free": "{{$a.b}}"}
