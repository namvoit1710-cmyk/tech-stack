"""Agent control-plane message vocabulary (SA-892).

Control messages carry out-of-band commands for an in-flight conversation —
today only a cooperative *cancel* (stop). They are deliberately transport-level
and tiny so the consumer intake can handle them inline, WITHOUT acquiring an
in-flight concurrency slot, so a stop is never starved behind the running turn
(business agents run with ``max_in_flight=1``).

Both the layer3 consumer presenter and the layer4 broker clients import from
here, so this lives in the domain layer to keep the dependency direction sane.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Message ``type`` that marks a control-plane command.
AGENT_CONTROL_MESSAGE_TYPE = "agent.control"

# The only control action so far: cooperatively cancel the conversation's turn.
AGENT_CANCEL_ACTION = "cancel"

# Message types the control plane owns. Kept as a frozenset to mirror
# ``EXECUTE_MESSAGE_TYPES`` and to leave room for future control types.
CONTROL_MESSAGE_TYPES = frozenset({AGENT_CONTROL_MESSAGE_TYPE})


def _read_str(value: Any, *keys: str) -> str:
    if not isinstance(value, Mapping):
        return ""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def is_agent_control_message(payload: Any) -> bool:
    """True when ``payload`` is an agent control-plane message."""
    return _read_str(payload, "type") in CONTROL_MESSAGE_TYPES


def is_cancel_control_message(payload: Any) -> bool:
    """True when ``payload`` is a control message requesting a cancel/stop.

    An explicit ``action`` of ``cancel`` is honored; a control message with no
    action defaults to cancel, since that is the only action today.
    """
    if not is_agent_control_message(payload):
        return False
    action = _read_str(payload, "action", "command")
    return action == AGENT_CANCEL_ACTION or action == ""


def extract_conv_id(payload: Any) -> str:
    """Extract the conversation id from any inbound message payload.

    ``conv_id == conversation_id == thread_id == correlation_id`` for agents, so
    we accept any of them (in priority order). This is the key the dispatch
    runtime registers turns under, and the key a control message cancels.
    """
    execution_context = (
        payload.get("execution_context") if isinstance(payload, Mapping) else None
    )
    return (
        _read_str(payload, "conv_id", "conversation_id", "thread_id")
        or _read_str(execution_context, "conversation_id", "conv_id")
        or _read_str(payload, "correlation_id")
    )


# Backwards/intent-friendly alias: a control message targets a conversation id.
control_conv_id = extract_conv_id
