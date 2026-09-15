"""WAIT spec — suspends the run until a durable timer fires (SA-1371 S4).

A WAIT node parks the run (``runtime="pending"``, exactly like INPUT /
HUMAN_ACTION) and schedules a durable ``Timer`` keyed to (run_id, node_id).
The leader-gated ``TimerDispatcher`` publishes ``TimerFired`` when ``fire_at``
elapses; the ``TimerFiredConsumer`` then resumes the run through the same
``resume_run`` path a user-submit of an INPUT node takes.

``mode`` selects how ``fire_at`` is computed:

* ``delay`` — ``now + delay_seconds``
* ``until`` — the ISO-8601 ``until`` timestamp verbatim
* ``sla``   — ``now + sla_timeout_seconds``
"""

from dataclasses import dataclass
from typing import Any, Optional

from app.layer1_domain.node_spec.model import (
    Choice,
    ConfigField,
    FieldType,
    NodeSpec,
    VisibleWhen,
    Widget,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

_MODE_CHOICES = (
    Choice("delay", "Delay"),
    Choice("until", "Until timestamp"),
    Choice("sla", "SLA timeout"),
)

WAIT_SPEC = register(NodeSpec(
    node_type=NodeType.WAIT,
    name="Wait",
    description="Suspends the run until a durable timer fires (delay / until / SLA).",
    category="control_flow.timer",
    runtime="pending",
    node_class=NodeClass.TECHNICAL,
    icon="Clock",
    color="#6366F1",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="mode", type=FieldType.STRING, label="Wait Mode",
            widget=Widget.SELECT, choices=_MODE_CHOICES,
            default="delay", form_default="delay", required=True,
        ),
        ConfigField(
            key="delay_seconds", type=FieldType.NUMBER, label="Delay (seconds)",
            widget=Widget.NUMBER, placeholder="60",
            visible_when=VisibleWhen(field="mode", equals="delay"),
        ),
        ConfigField(
            key="until", type=FieldType.STRING, label="Until (ISO-8601)",
            placeholder="2026-07-01T00:00:00+00:00",
            visible_when=VisibleWhen(field="mode", equals="until"),
        ),
        ConfigField(
            key="sla_timeout_seconds", type=FieldType.NUMBER,
            label="SLA Timeout (seconds)", widget=Widget.NUMBER,
            placeholder="3600",
            visible_when=VisibleWhen(field="mode", equals="sla"),
        ),
    ),
    output_fields=(),
))


@dataclass(frozen=True)
class WaitConfig:
    mode: str = "delay"
    delay_seconds: Optional[int] = None
    until: str = ""
    sla_timeout_seconds: Optional[int] = None

    @classmethod
    def from_node(cls, node: Any) -> "WaitConfig":
        return parse_config(WAIT_SPEC, cls, node)
