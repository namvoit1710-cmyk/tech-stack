from __future__ import annotations

from app.layer4_frameworks.clients.http_catalog_provider import HttpCatalogProvider

# Real control-plane shape: enveloped {status,data}, node_type (snake_case), ports.outputs.
CATALOG_RESPONSE = {
    "status": "ok",
    "data": {"definitions": [
        {"id": "builtin:condition", "name": "Condition", "description": "branch",
         "node_type": "CONDITION", "runtime": "engine", "icon": "GitBranch", "color": "#8B5CF6",
         "ports": {"inputs": [{"id": "in_default"}], "outputs": [{"id": "true_branch"}, {"id": "false_branch"}]}},
        {"id": "worker:http_request", "name": "HTTP", "node_type": "TASK"},  # no icon/color -> defaults
        {"name": "no-id"},  # dropped
    ]},
}


def test_parse_nodes_unwraps_envelope_and_real_fields() -> None:
    nodes = HttpCatalogProvider._parse_nodes(CATALOG_RESPONSE)
    assert len(nodes) == 2
    assert nodes[0].node_type == "CONDITION"
    assert nodes[0].runtime == "engine"
    assert nodes[0].in_ports == ["in_default"]
    assert nodes[0].out_ports == ["true_branch", "false_branch"]
    # display fields the control plane persists verbatim: passed through, or defaulted
    assert nodes[0].icon == "GitBranch" and nodes[0].color == "#8B5CF6"
    assert nodes[1].icon == "Cog" and nodes[1].color == "#3B82F6"


# Detail response: field names are snake_case (model_dump without by_alias), FormField
# carries a rules list with a "required" method and control_props options for selects.
DETAIL = {
    "id": "builtin:trigger",
    "node_type": "TRIGGER",
    "name": "Trigger",
    "description": "start",
    "ports": {"inputs": [], "outputs": [{"id": "out_default"}]},
    "input_schema": [
        {"key": "trigger_type", "output_type": "string", "field_config": {
            "field_control": "SelectControl",
            "wrapper_props": {"label": "Trigger Type"},
            "rules": [{"method": "required"}],
            "control_props": {"placeholder": "Select trigger type",
                              "options": [{"label": "Manual", "value": "manual"},
                                          {"label": "Schedule", "value": "schedule"}]},
        }},
        {"key": "note", "output_type": "string", "field_config": {"field_control": "InputControl"}},
    ],
    "output_schema": [
        {"key": "trigger_payload", "output_type": "object", "field_config": {}},
    ],
}


def test_parse_schema_extracts_fields_options_and_ports() -> None:
    schema = HttpCatalogProvider._parse_schema(DETAIL)
    assert schema.node_type == "TRIGGER"
    assert schema.out_ports == ["out_default"]
    assert schema.output_fields == ["trigger_payload"]
    by_key = {f.key: f for f in schema.config_fields}
    assert by_key["trigger_type"].required is True
    assert by_key["trigger_type"].options == ["manual", "schedule"]
    assert by_key["trigger_type"].control == "SelectControl"
    assert by_key["note"].required is False


def test_parse_nodes_reads_browse_taxonomy() -> None:
    # SA-2210: the /node-catalog definition carries category/kind/collection/tags; parse them.
    data = {"status": "ok", "data": {"definitions": [
        {"id": "builtin:input", "name": "Input", "node_type": "INPUT",
         "category": "Core.human", "kind": "human", "collection": "Core",
         "tags": ["form", "hitl"]},
        {"id": "worker:x", "name": "X", "node_type": "TASK"},  # missing -> defaults
    ]}}
    nodes = HttpCatalogProvider._parse_nodes(data)
    assert (nodes[0].category, nodes[0].kind, nodes[0].collection) == ("Core.human", "human", "Core")
    assert nodes[0].tags == ["form", "hitl"]
    assert (nodes[1].category, nodes[1].kind, nodes[1].collection, nodes[1].tags) == ("", "", "", [])


def test_parse_schema_reads_field_label_and_placeholder() -> None:
    # SA-2210: label (wrapper_props.label) + placeholder (control_props.placeholder) are the
    # human field help the control-plane form renderer emits.
    schema = HttpCatalogProvider._parse_schema(DETAIL)
    by_key = {f.key: f for f in schema.config_fields}
    assert by_key["trigger_type"].label == "Trigger Type"
    assert by_key["trigger_type"].placeholder == "Select trigger type"


def test_parse_schema_tolerates_camelcase_and_nested_fields() -> None:
    detail = {
        "id": "builtin:output", "nodeType": "OUTPUT",
        "inputSchema": [
            {"key": "output_schema", "outputType": "array", "fieldConfig": {
                "fieldControl": "TextareaControl",
            }, "fields": [
                {"key": "value", "outputType": "string", "fieldConfig": {}},
            ]},
        ],
    }
    schema = HttpCatalogProvider._parse_schema(detail)
    assert schema.node_type == "OUTPUT"
    nested = schema.config_fields[0].fields
    assert [f.key for f in nested] == ["value"]


def test_details_of_reads_batch_envelope() -> None:
    data = {"status": "ok", "data": {"details": [DETAIL], "missing": []}}
    details = HttpCatalogProvider._details_of(data)
    assert len(details) == 1 and details[0]["id"] == "builtin:trigger"


def test_parse_nodes_tolerates_bad_input() -> None:
    assert HttpCatalogProvider._parse_nodes(None) == []
    assert HttpCatalogProvider._parse_nodes({"status": "ok", "data": {}}) == []


def test_parse_workers_unwraps_envelope() -> None:
    data = {"status": "ok", "data": {"agents": [
        {"id": "http-worker-v1", "name": "HTTP W", "capabilities": ["http-request"]},
    ]}}
    workers = HttpCatalogProvider._parse_workers(data)
    assert workers[0].id == "http-worker-v1"
    assert workers[0].capabilities == ["http-request"]
