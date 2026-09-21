"""Persist-then-push notifier for the orchestrator.

Mirrors the business-agent PushGatewayNotifier: high-level send methods build a UIEvent,
persist it to the event repo (save_event + upsert_timeline_event) BEFORE pushing it via
the raw agent_sdk notifier. Without this, a reply the orchestrator sends directly (no
business agent runs) is pushed to the UI but never stored, so it is missing from
conversation history on the next turn — which breaks follow-ups and ambiguity replies.
"""
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Any

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)

from app.layer1_domain.entities.orchestrator.turn_meter import get_turn_meter
from app.layer1_domain.entities.orchestrator.ui_event import (
    UIEvent,
    UIEventRole,
    UIEventType,
    UIMetadata,
    UIPayload,
    UIPayloadType,
)
from app.layer1_domain.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.agent_event_repository import IAgentEventRepository
from app.layer2_application.interfaces.push_gateway_notifier import IPushGatewayNotifier


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime) or hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


class PersistingPushGatewayNotifier(IPushGatewayNotifier):
    """Decorates the raw agent_sdk notifier, persisting orchestrator-originated events on send."""

    def __init__(
        self,
        notifier: PushGatewayNotifierProtocol,
        agent_event_repo: IAgentEventRepository,
        uuid_builder: Any,
        logger: ILogger,
    ) -> None:
        self._notifier = notifier
        self._agent_event_repo = agent_event_repo
        self._uuid_builder = uuid_builder
        self._logger = logger

    # ---- raw protocol passthrough (dict payloads from other callers; not persisted) ----
    async def send_notification(self, key: str, data: Any, *, is_final: bool = False) -> None:
        await self._notifier.send_notification(key, data, is_final=is_final)

    async def close(self) -> None:
        await self._notifier.close()

    # ---- high-level ----
    async def send_chat_response(self, conv_id: str, content: str) -> None:
        # Persist-then-push: a direct orchestrator answer must survive into history.
        # SA-2183: the answer is pushed first; the settled "Cooked for…" token_info (reusing
        # the turn's message_id) is emitted LAST — after every other chat:response of the turn.
        meter = get_turn_meter()
        event_id = meter.message_id if meter is not None else self._uuid_builder.create_uuid7()
        ui_event = UIEvent(
            event_id=event_id,
            role=UIEventRole.ASSISTANT,
            event_type=UIEventType.CHAT_RESPONSE,
            conv_id=conv_id,
            metadata=UIMetadata(main_conv_id=conv_id),
            payload=UIPayload(id=event_id, content=content),
            correlation_id=event_id,
        )
        self._persist(ui_event)
        await self._push(ui_event)
        if meter is not None:
            await self.send_token_info(
                conv_id=meter.conv_id or conv_id,
                message_id=meter.message_id,
                content=meter.render(done=True),
                status="success",
            )

    async def send_token_info(
        self, conv_id: str, message_id: str, content: str, status: str
    ) -> None:
        # SA-2183: in-progress ("processing") emits as chat:thinking, push-only — like
        # send_chat_thinking, it must NOT be persisted (a transient progress indicator the FE
        # collapses into one in-place line and drops once the turn settles; persisting it would
        # replay a stale "Cooking…" line on the next history load). No payload.type is set so
        # the FE's persistent-message filter (`!status || payload.type`) drops it too.
        # The settled ("success") emit stays chat:response and is persisted, unchanged from
        # SA-2079, so the "Cooked for…" receipt survives into history.
        is_processing = status == "processing"
        event_id = self._uuid_builder.create_uuid7()
        ui_event = UIEvent(
            event_id=event_id,
            role=UIEventRole.ASSISTANT,
            event_type=UIEventType.CHAT_THINKING if is_processing else UIEventType.CHAT_RESPONSE,
            conv_id=conv_id,
            metadata=UIMetadata(main_conv_id=conv_id),
            payload=UIPayload(
                id=event_id,
                message_id=message_id,
                content=content,
                type=None if is_processing else UIPayloadType.TOKEN_INFO,
                status=status,
            ),
            correlation_id=event_id,
        )
        if is_processing:
            await self._push(ui_event)
        else:
            self._persist(ui_event)
            await self._push(ui_event)

    async def send_chat_thinking(self, conv_id: str, content: str) -> None:
        # Push-only: "thinking" is a transient progress indicator. It must NOT be persisted —
        # writing it to the timeline (AIW_CHAT_TIMELINE_NODE) makes the FE load it as a permanent
        # history message. (The business agent's thinking is task-scoped, so it renders as an
        # in-task step; the orchestrator's is top-level and would show as a standalone bubble.)
        event_id = self._uuid_builder.create_uuid7()
        ui_event = UIEvent(
            event_id=event_id,
            role=UIEventRole.ASSISTANT,
            event_type=UIEventType.CHAT_THINKING,
            conv_id=conv_id,
            metadata=UIMetadata(main_conv_id=conv_id),
            payload=UIPayload(id=event_id, content=content, status=TaskStatus.SUCCESS),
            correlation_id=event_id,
        )
        await self._push(ui_event)

    def _persist(self, ui_event: UIEvent) -> None:
        # Best-effort — a store failure must not block the user-facing push.
        try:
            self._agent_event_repo.save_event(ui_event)
            self._agent_event_repo.upsert_timeline_event(ui_event)
            self._logger.info(
                "Persisted outbound UI event",
                conv_id=ui_event.conv_id,
                event_type=ui_event.event_type.value,
                role=ui_event.role.value,
            )
        except Exception as exc:
            self._logger.error(
                "Failed to persist outbound UI event",
                conv_id=ui_event.conv_id,
                event_type=ui_event.event_type.value,
                error=str(exc),
            )

    async def _push(self, ui_event: UIEvent) -> None:
        await self._notifier.send_notification(
            key=ui_event.conv_id, data=_json_safe(asdict(ui_event)), is_final=False
        )
