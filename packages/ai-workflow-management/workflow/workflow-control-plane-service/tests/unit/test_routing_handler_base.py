"""Unit tests for ``RoutingHandlerBase``'s template method.

The base is exercised via a synthetic subclass whose ``select_port``
returns a deterministic :class:`RoutingDecision`. We assert that:

* ``completion_mode`` is ``"immediate"``.
* ``state_updates[node.id]`` carries the family metadata.
* ``taken_edge_ports`` is populated only for the edges the
  ``edge_accepts`` closure accepts.
* ``completion_payload`` from the decision lands on the result.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.layer2_application.node_handlers.base_node_handler import RoutingDecision
from app.layer2_application.node_handlers.routing_base import RoutingHandlerBase


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

@dataclass
class _Edge:
    id: str
    source_port_id: str
    condition: str = ""


class _Topology:
    def __init__(self, edges):
        self.edge_map = {"n1": edges}


class _Node:
    id = "n1"


class _Run:
    pass


# ---------------------------------------------------------------------------
# Fixture subclass
# ---------------------------------------------------------------------------

class _StubRouting(RoutingHandlerBase):
    METADATA_TYPE = "stub"

    def __init__(self, decision: RoutingDecision) -> None:
        self._decision = decision

    async def select_port(self, node, run):
        return self._decision


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_routing_handler_populates_state_and_completion_payload():
    decision = RoutingDecision(
        matched_branch="then",
        matched_port_id="",
        completion_payload={"branch": "then", "condition_result": True},
        edge_accepts=lambda _e: None,
    )
    handler = _StubRouting(decision)
    edges = [
        _Edge(id="e1", source_port_id="true_branch"),
        _Edge(id="e2", source_port_id="false_branch"),
    ]
    result = await handler.handle(
        _Node(), _Run(), workflow=None,
        topology=_Topology(edges),
        completed_task=None, task_factory=None,
    )
    assert result.completion_mode == "immediate"
    assert result.state_updates["n1"] == {
        "output": {},
        "metadata": {"type": "stub", "taken_edge_ports": {}},
    }
    assert result.completion_payload == {"branch": "then", "condition_result": True}
    # edge_accepts returns None for every edge → no edges taken
    assert result.taken_edge_ports == {}


@pytest.mark.asyncio
async def test_routing_handler_records_accepted_edges_with_port_labels():
    def accepts(edge):
        if edge.source_port_id == "true_branch":
            return edge.source_port_id
        return None

    handler = _StubRouting(RoutingDecision(
        matched_branch="then", matched_port_id="",
        completion_payload={}, edge_accepts=accepts,
    ))
    edges = [
        _Edge(id="e_true", source_port_id="true_branch"),
        _Edge(id="e_false", source_port_id="false_branch"),
        _Edge(id="e_other", source_port_id="weird"),
    ]
    result = await handler.handle(
        _Node(), _Run(), workflow=None,
        topology=_Topology(edges),
        completed_task=None, task_factory=None,
    )
    assert result.taken_edge_ports == {"e_true": "true_branch"}


@pytest.mark.asyncio
async def test_routing_handler_default_metadata_type():
    """The base ``METADATA_TYPE`` is ``"routing"``; subclasses override."""
    class _Plain(RoutingHandlerBase):
        async def select_port(self, node, run):
            return RoutingDecision(
                matched_branch=None, matched_port_id="",
                completion_payload={}, edge_accepts=lambda e: None,
            )

    handler = _Plain()
    result = await handler.handle(
        _Node(), _Run(), workflow=None,
        topology=_Topology([]),
        completed_task=None, task_factory=None,
    )
    assert result.state_updates["n1"]["metadata"] == {"type": "routing", "taken_edge_ports": {}}
