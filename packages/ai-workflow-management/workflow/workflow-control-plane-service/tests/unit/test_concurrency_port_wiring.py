"""MERGE fan-in / PARALLEL fan-out get a distinct declared port per branch.

The engine routes these by edge (topology.predecessors / branches), so their NodeSpec
declares no per-branch ports — but the FE renders/validates by ports and drops edges that
reference a port the node doesn't declare (the generic in_default/out_default). These tests
pin the fix: synthesize a distinct named port per branch and declare it on the node.
"""

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.definition.concurrency_ports import wire_concurrency_ports


def _wf(nodes, edges):
    return Workflow(name="wf", routing_path="/", nodes=nodes, edges=edges)


def test_merge_fanin_gets_distinct_named_ports():
    merge = WorkflowNode(id="merge1", node_type=NodeType.MERGE,
                         in_ports=[], out_ports=[{"id": "out_default", "label": "Output"}])
    t1 = WorkflowNode(id="t1", node_type=NodeType.TASK)
    t2 = WorkflowNode(id="t2", node_type=NodeType.TASK)
    out = WorkflowNode(id="out1", node_type=NodeType.OUTPUT,
                       in_ports=[{"id": "in_default", "label": "Input"}], out_ports=[])
    edges = [
        WorkflowEdge(source_node_id="t1", target_node_id="merge1", target_port_id="in_default"),
        WorkflowEdge(source_node_id="t2", target_node_id="merge1", target_port_id="in_default"),
        WorkflowEdge(source_node_id="merge1", target_node_id="out1",
                     source_port_id="out_default", target_port_id="in_default"),
    ]
    wf = _wf([merge, t1, t2, out], edges)

    wire_concurrency_ports(wf)

    fanin = [e for e in wf.edges if e.target_node_id == "merge1"]
    assert {e.target_port_id for e in fanin} == {"in_t1", "in_t2"}  # distinct, not shared in_default
    assert {p["id"] for p in merge.ports["in"]} == {"in_t1", "in_t2"}  # declared on the node
    # merge -> output edge is untouched (output's in_default is a real port)
    e_out = next(e for e in wf.edges if e.source_node_id == "merge1")
    assert e_out.target_port_id == "in_default"


def test_parallel_fanout_gets_branch_ports():
    par = WorkflowNode(id="p1", node_type=NodeType.PARALLEL,
                       in_ports=[{"id": "in_default"}], out_ports=[])
    a = WorkflowNode(id="a", node_type=NodeType.TASK)
    b = WorkflowNode(id="b", node_type=NodeType.TASK)
    edges = [
        WorkflowEdge(source_node_id="p1", target_node_id="a", source_port_id="out_default"),
        WorkflowEdge(source_node_id="p1", target_node_id="b", source_port_id=""),
    ]
    wf = _wf([par, a, b], edges)

    wire_concurrency_ports(wf)

    assert {e.source_port_id for e in wf.edges} == {"branch_a", "branch_b"}
    assert {p["id"] for p in par.ports["out"]} == {"branch_a", "branch_b"}


def test_prenamed_ports_are_kept_and_not_duplicated():
    merge = WorkflowNode(id="m", node_type=NodeType.MERGE,
                         in_ports=[{"id": "in_x", "label": "X"}],
                         out_ports=[{"id": "out_default"}])
    t = WorkflowNode(id="t", node_type=NodeType.TASK)
    edges = [WorkflowEdge(source_node_id="t", target_node_id="m", target_port_id="in_x")]
    wf = _wf([merge, t], edges)

    wire_concurrency_ports(wf)

    assert edges[0].target_port_id == "in_x"  # semantic port respected
    assert sum(1 for p in merge.ports["in"] if p["id"] == "in_x") == 1  # not duplicated


def test_non_concurrency_edges_untouched():
    trig = WorkflowNode(id="trig", node_type=NodeType.TRIGGER,
                        in_ports=[], out_ports=[{"id": "out_default"}])
    task = WorkflowNode(id="task", node_type=NodeType.TASK,
                        in_ports=[{"id": "in_default"}], out_ports=[{"id": "success"}])
    edges = [WorkflowEdge(source_node_id="trig", target_node_id="task",
                          source_port_id="out_default", target_port_id="in_default")]
    wf = _wf([trig, task], edges)

    wire_concurrency_ports(wf)

    assert edges[0].source_port_id == "out_default"
    assert edges[0].target_port_id == "in_default"
