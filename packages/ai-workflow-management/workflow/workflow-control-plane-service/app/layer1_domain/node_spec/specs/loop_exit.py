"""LOOP_EXIT / LOOP_CONTINUE specs — body-branch dead-end signal nodes.

No config, no outputs; declared so the spec registry fully covers the
legacy NodeTypeDescriptor table (deleted in Phase 5).
"""

from app.layer1_domain.node_spec.model import NodeSpec
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

LOOP_EXIT_SPEC = register(NodeSpec(
    node_type=NodeType.LOOP_EXIT,
    name="Loop Exit",
    description="Breaks out of the enclosing loop.",
    category="control_flow.iteration",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="CircleStop",
    color="#EF4444",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(),
    min_out_ports=0,
    max_out_ports=0,
))

LOOP_CONTINUE_SPEC = register(NodeSpec(
    node_type=NodeType.LOOP_CONTINUE,
    name="Loop Continue",
    description="Skips to the next iteration of the enclosing loop.",
    category="control_flow.iteration",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="SkipForward",
    color="#8B5CF6",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(),
    min_out_ports=0,
    max_out_ports=0,
))
