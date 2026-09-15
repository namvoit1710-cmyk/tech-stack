"""Spec-routed node/workflow builders for tests (NodeSpec Phase 5, T5.4).

``make_node`` constructs nodes the way the API does — a flat
``parameters`` dict routed through the spec-driven split — so tests
cannot hand-roll config/input_binding layouts that drift from
production behavior. Use explicit ``config=``/``input_binding=`` only
when a test deliberately targets engine-internal layout.
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.workflow import (
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from app.layer1_domain.value_objects.node_type import NodeType


def make_node(
    node_type: NodeType | str,
    *,
    node_id: str = "n1",
    name: str = "",
    worker_type: str | None = None,
    **params: Any,
) -> WorkflowNode:
    """Build a WorkflowNode from FE-style ``parameters`` kwargs.

    ``make_node(NodeType.MERGE, merge_strategy="n_of_m", wait_count=2)``
    goes through the real ``split_parameters``, landing keys exactly
    where a saved workflow would have them.
    """
    return WorkflowNode(
        id=node_id,
        workflow_id=node_id,
        name=name or node_id,
        node_type=node_type if isinstance(node_type, NodeType) else NodeType(node_type),
        worker_type=worker_type,
        parameters=dict(params),
    )


def make_workflow(
    nodes: list[WorkflowNode],
    edges: list[tuple[str, str]] | list[WorkflowEdge] | None = None,
    *,
    workflow_id: str = "wf-1",
    name: str = "Test Workflow",
) -> Workflow:
    """Build a Workflow; edges may be (source, target) tuples."""
    edge_entities: list[WorkflowEdge] = []
    for e in edges or []:
        if isinstance(e, WorkflowEdge):
            edge_entities.append(e)
        else:
            source, target = e
            edge_entities.append(
                WorkflowEdge(source_node_id=source, target_node_id=target),
            )
    return Workflow(id=workflow_id, name=name, nodes=nodes, edges=edge_entities)
