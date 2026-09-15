"""TRIGGER spec — workflow entry point.

All five fields are CONFIG (the legacy `_CONFIG_KEYS` omitted
``webhook_path``/``event_filter``, silently routing them to
``input_binding`` where nothing read them). The conditionally visible
fields are ``required`` — enforced by save-time validation only when
visible; the rendered form is unchanged (the renderer does not emit
``required`` for `visible_when` fields, see form_renderer).
"""

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.model import (
    Choice,
    ConfigField,
    FieldType,
    NodeSpec,
    OutputField,
    VisibleWhen,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

TRIGGER_SPEC = register(NodeSpec(
    node_type=NodeType.TRIGGER,
    name="Trigger",
    description="Entry point that starts workflow execution on an event or schedule.",
    category="control_flow.entry",
    runtime="engine",
    node_class=NodeClass.TECHNICAL,
    icon="Zap",
    color="#F59E0B",
    in_ports=(),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="trigger_type", type=FieldType.STRING, label="Trigger Type",
            required=True, default="manual",
            # SA-2059: Webhook is retired as a node trigger_type. Every external
            # trigger is now an EVENT — a webhook is an ingress *registration*
            # (Settings → Triggers) that publishes onto an event_topic, and the
            # workflow subscribes with trigger_type=event. Manual/Schedule/Event only.
            choices=(Choice("manual", "Manual"),
                     Choice("schedule", "Schedule"),
                     Choice("event", "Event")),
        ),
        ConfigField(
            key="schedule_cron", type=FieldType.STRING, label="Cron Schedule",
            placeholder="0 9 * * *", required=True,
            visible_when=VisibleWhen(field="trigger_type", equals="schedule"),
        ),
        ConfigField(
            key="event_topic", type=FieldType.STRING, label="Event Topic",
            placeholder="orders.created", required=True,
            visible_when=VisibleWhen(field="trigger_type", equals="event"),
        ),
        ConfigField(
            key="event_filter", type=FieldType.STRING, label="Event Filter",
            # F6: the placeholder MUST match the evaluator's syntax. The
            # ConditionEvaluator keys off the {{$root.path}} token; the consumer
            # passes two roots — $event (the payload) and $trigger (the full
            # envelope: event_id, event_topic, tenant_id, source, ...). A
            # JSONPath like "$.data.priority" resolves to a literal string and
            # compares always-false, so the workflow never triggers.
            placeholder="{{$event.priority}} == 'high'", expression=True,
            description=(
                "Only trigger when this expression is true. Reference the event "
                "payload as {{$event.<field>}} and envelope metadata as "
                "{{$trigger.<field>}} (e.g. {{$trigger.source}}). Combine with "
                "AND / OR. Leave blank to trigger on every matching event."
            ),
            visible_when=VisibleWhen(field="trigger_type", equals="event"),
        ),
        # SA-2059: webhook_path removed — webhooks are ingress registrations now,
        # not a node config field. TriggerConfig keeps the attribute for
        # backward-compatible parsing of any workflow saved before the cutover.
    ),
    output_fields=(
        OutputField("trigger_type", FieldType.STRING, "Trigger Type"),
        OutputField("triggered_at", FieldType.STRING, "Triggered At"),
        OutputField("trigger_payload", FieldType.OBJECT, "Trigger Payload"),
        OutputField("correlation_id", FieldType.STRING, "Correlation ID"),
    ),
))


@dataclass(frozen=True)
class TriggerConfig:
    trigger_type: str = "manual"
    schedule_cron: str = ""
    event_topic: str = ""
    event_filter: str = ""
    # webhook_path removed (SA-2059): webhooks are ingress registrations, not a
    # node trigger_type. Kept out of the typed config to satisfy the spec-drift
    # guard; parse_config ignores any lingering key in old persisted configs.

    @classmethod
    def from_node(cls, node: Any) -> "TriggerConfig":
        return parse_config(TRIGGER_SPEC, cls, node)
