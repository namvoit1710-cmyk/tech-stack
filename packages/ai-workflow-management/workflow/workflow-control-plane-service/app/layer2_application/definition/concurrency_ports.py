"""Give MERGE fan-in / PARALLEL fan-out edges a distinct, declared port per branch.

MERGE and PARALLEL route by EDGE — the engine reads ``topology.predecessors`` (merge) and
the parallel ``branches`` — so their NodeSpec deliberately declares no per-branch input /
output ports (``in_ports=()`` / ``out_ports=()``). The port model the FE renders and
validates against, however, needs a declared port for every edge endpoint: the generic
``in_default`` / ``out_default`` does not exist on these nodes, so an edge referencing it is
port-invalid and gets dropped when the graph is reconciled / auto-laid-out, silently
disconnecting the merge (and its downstream).

This runs at save time (create + update), after the spec's default ports are applied, and
synthesizes a distinct named port per branch — ``in_<source>`` on a MERGE, ``branch_<target>``
on a PARALLEL — declaring it on the node so the persisted graph is port-consistent. It does
NOT change execution (the engine still routes by edge); it only makes the declared ports
match the edges. Idempotent: an already-named port (e.g. ``in_payment``) is kept and only
ensured to exist.
"""
from __future__ import annotations

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType


def wire_concurrency_ports(workflow: Workflow) -> None:
    by_id = {n.id: n for n in workflow.nodes}
    for edge in workflow.edges:
        target = by_id.get(edge.target_node_id)
        if target is not None and target.node_type == NodeType.MERGE:
            port_id = (edge.target_port_id
                       if edge.target_port_id not in ("", "in_default")
                       else f"in_{edge.source_node_id}")
            edge.target_port_id = port_id
            _ensure_port(target, "in", port_id, f"In: {edge.source_node_id}")

        source = by_id.get(edge.source_node_id)
        if source is not None and source.node_type == NodeType.PARALLEL:
            port_id = (edge.source_port_id
                       if edge.source_port_id not in ("", "out_default")
                       else f"branch_{edge.target_node_id}")
            edge.source_port_id = port_id
            _ensure_port(source, "out", port_id, f"Branch: {edge.target_node_id}")


def _ensure_port(node: WorkflowNode, side: str, port_id: str, label: str) -> None:
    """Append a port to the node's in/out list (and re-sync ``ports``) if not already present."""
    ports = node.in_ports if side == "in" else node.out_ports
    if any(isinstance(p, dict) and p.get("id") == port_id for p in ports):
        return
    ports.append({"id": port_id, "label": label, "data_type": "any",
                  "required": False, "description": ""})
    node.ports = {"in": node.in_ports, "out": node.out_ports}
