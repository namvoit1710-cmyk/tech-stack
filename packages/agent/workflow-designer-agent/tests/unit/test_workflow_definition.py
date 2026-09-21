from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_definition import (
    WorkflowDefinition,
    WorkflowValidationError,
)

VALID_NODES = [
    {"id": "trigger", "type": "trigger", "name": "On file upload", "config": {}},
    {"id": "validate", "type": "validate", "name": "Validate CSV", "config": {"rules": ["not_empty"]}},
]
VALID_EDGES = [{"source": "trigger", "target": "validate"}]


def test_from_raw_builds_valid_definition() -> None:
    definition = WorkflowDefinition.from_raw("CSV check", "Validate an uploaded CSV", VALID_NODES, VALID_EDGES)
    document = definition.to_document()
    assert document["name"] == "CSV check"
    assert [node["id"] for node in document["nodes"]] == ["trigger", "validate"]
    assert document["edges"] == [{"source": "trigger", "target": "validate"}]


def test_missing_name_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError):
        WorkflowDefinition.from_raw("  ", "d", VALID_NODES, VALID_EDGES)


def test_single_node_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError):
        WorkflowDefinition.from_raw("X", "d", [VALID_NODES[0]], [])


def test_duplicate_node_ids_are_rejected() -> None:
    nodes = [VALID_NODES[0], {"id": "trigger", "type": "email", "name": "dup", "config": {}}]
    with pytest.raises(WorkflowValidationError):
        WorkflowDefinition.from_raw("X", "d", nodes, [])


def test_edge_referencing_unknown_node_is_rejected() -> None:
    edges = [{"source": "trigger", "target": "ghost"}]
    with pytest.raises(WorkflowValidationError):
        WorkflowDefinition.from_raw("X", "d", VALID_NODES, edges)


def test_isolated_node_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError):
        WorkflowDefinition.from_raw("X", "d", VALID_NODES, [])


def test_self_loop_edge_is_rejected() -> None:
    edges = [{"source": "trigger", "target": "trigger"}]
    with pytest.raises(WorkflowValidationError):
        WorkflowDefinition.from_raw("X", "d", VALID_NODES, edges)
