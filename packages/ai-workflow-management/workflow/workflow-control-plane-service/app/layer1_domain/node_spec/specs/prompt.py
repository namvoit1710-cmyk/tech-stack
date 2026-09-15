"""PROMPT spec — a single-textarea data feed for downstream LLM/agent nodes.

The node holds free text (which may contain ``{{...}}`` references). At
runtime the handler resolves the text and emits it under the node's own
output namespace, readable downstream as ``{{$<nodeId>.output.content}}``.
It is a passive source: it never calls an LLM itself.
"""

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
    OutputField,
    Widget,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

PROMPT_SPEC = register(NodeSpec(
    node_type=NodeType.PROMPT,
    name="Prompt",
    description="Holds free text (with {{...}} references) to feed a downstream LLM/agent node.",
    category="ai.prompt",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Sparkles",
    color="#8B5CF6",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="content", type=FieldType.STRING, label="Content",
            widget=Widget.TEXTAREA, required=True, expression=True,
            placeholder="Summarize: {{$node-1.output.text}}",
        ),
    ),
    output_fields=(
        OutputField("content", FieldType.STRING, "Content"),
    ),
))


@dataclass(frozen=True)
class PromptConfig:
    content: str = ""

    @classmethod
    def from_node(cls, node: Any) -> "PromptConfig":
        return parse_config(PROMPT_SPEC, cls, node)
