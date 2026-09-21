from datetime import datetime
from typing import List, Optional

from app.layer1_domain.entities.next_step_suggestion import NextStepSuggestion
from app.layer1_domain.entities.turn_meter import get_turn_meter
from app.layer1_domain.entities.ui_event import (
    UIActionReference,
    UIPayload,
    UIMetadata,
    UIEvent,
    UIEventType,
    UIPayloadType,
)
from app.layer2_application.interfaces.push_gateway_notifier_interface import (
    IPushGatewayNotifier,
)
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder
from app.layer2_application.interfaces.ui_event_repository_interface import IUIEventRepository
from app.layer2_application.interfaces.chat_timeline_node_repository_interface import IChatTimelineNodeRepository
from app.layer2_application.interfaces.logger_interface import ILogger

from agent_sdk.layer1_domain.entities.agent_shared_state import Task, TaskStatus
from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)
from dataclasses import asdict
import json
from enum import Enum

class PushGatewayNotifier(IPushGatewayNotifier):
    def __init__(
        self,
        notifier: PushGatewayNotifierProtocol,
        logger: ILogger,
        uuid_builder: IUuidBuilder,
        ui_event_repository: IUIEventRepository,
        chat_timeline_node_repository: IChatTimelineNodeRepository,
    ) -> None:
        self._notifier = notifier
        self._logger = logger
        self._uuid_builder = uuid_builder
        self._ui_event_repository = ui_event_repository
        self._chat_timeline_node_repository = chat_timeline_node_repository

    async def send_notification(self, key: str, data: UIEvent) -> None:
        self._normalize_text_payload_status(data)
        self._ui_event_repository.save_event(data)
        self._chat_timeline_node_repository.upsert_from_event(data)
        # Convert Pydantic models to dict for SDK's JSON serialization
        payload = self._json_safe(asdict(data))
        await self._notifier.send_notification(key, payload, is_final=False)
        self._logger.info(
            f"Sent notification to Push Gateway with key: {key} and data: {payload}"
        )

    async def send_chat_disable_event(self, conv_id: str) -> None:
        await self.send_notification(
            key=conv_id,
            data=self._build_chat_toggle_event(conv_id, UIEventType.CHAT_DISABLE),
        )

    async def send_chat_enable_event(self, conv_id: str) -> None:
        await self.send_notification(
            key=conv_id,
            data=self._build_chat_toggle_event(conv_id, UIEventType.CHAT_ENABLE),
        )

    async def send_task_status_event(self, conv_id: str, task: Task) -> None:
        ui_metadata = UIMetadata(main_conv_id=conv_id)

        ui_payload = UIPayload(
            id=task.id,
            type=UIPayloadType.PROGRESSING_COLLAPSE,
            content=task.goal or "",
            status=task.status,
            parent_id=task.parent_task_id or "",
        )
        generated_id = self._uuid_builder.create_uuid7()
        ui_event = UIEvent(
            event_id=generated_id,
            correlation_id=generated_id,
            event_type=UIEventType.PROCESS_RESPONSE,
            conv_id=conv_id,
            metadata=ui_metadata,
            payload=ui_payload,
        )

        await self.send_notification(key=conv_id, data=ui_event)

    async def send_retry_button(self, conv_id: str, task: Task) -> None:
        generated_id = self._uuid_builder.create_uuid7()
        ui_metadata = UIMetadata(main_conv_id=conv_id)

        ui_payload = UIPayload(
            id=task.id,
            type=UIPayloadType.BUTTON_GROUP,
            content=task.goal or "",
            status=TaskStatus.PROCESSING,
            text=["Retry"],
        )
        ui_event = UIEvent(
            event_id=generated_id,
            correlation_id=generated_id,
            event_type=UIEventType.PROCESS_RESPONSE,
            conv_id=conv_id,
            metadata=ui_metadata,
            payload=ui_payload,
        )

        await self.send_notification(key=conv_id, data=ui_event)

    @staticmethod
    def _build_summary_content(
        summary: str, suggestions: Optional[List[NextStepSuggestion]]
    ) -> str:
        """Append suggestion titles to the summary text.

        The FE currently has no handler for next_step_suggestions in UIPayload,
        so the hints are embedded in content (whitespace-pre-line, plain text)
        for immediate visibility.  next_step_suggestions stays for future FE use.
        """
        if not suggestions:
            return summary
        hint_lines = "\n".join(f"• {s.title}" for s in suggestions)
        return f"{summary}\n\nSuggested next steps:\n{hint_lines}"

    async def send_task_summary_event(
        self,
        conv_id: str,
        summary: str,
        suggestions: Optional[List[NextStepSuggestion]] = None,
    ) -> None:
        # SA-2183: the final content is pushed first; the settled "Cooked for…" token_info
        # (reusing the turn's message_id) is emitted LAST — after every other chat:response.
        meter = get_turn_meter()
        generated_id = meter.message_id if meter is not None else self._uuid_builder.create_uuid7()
        ui_metadata = UIMetadata(main_conv_id=conv_id)

        ui_payload = UIPayload(
            id=generated_id,
            content=self._build_summary_content(summary, suggestions),
            status=TaskStatus.SUCCESS,
            next_step_suggestions=suggestions or None,
        )
        ui_event = UIEvent(
            event_id=generated_id,
            correlation_id=generated_id,
            event_type=UIEventType.CHAT_RESPONSE,
            conv_id=conv_id,
            metadata=ui_metadata,
            payload=ui_payload,
        )
        await self.send_notification(key=conv_id, data=ui_event)
        if meter is not None:
            await self.send_token_info_event(
                conv_id=meter.conv_id or conv_id,
                message_id=meter.message_id,
                content=meter.render(done=True),
                status=TaskStatus.SUCCESS,
            )
        await self.send_chat_enable_event(conv_id)

    async def send_token_info_event(
        self, conv_id: str, message_id: str, content: str, status: TaskStatus
    ) -> None:
        # SA-2183: in-progress (processing) emits as chat:thinking, push-only — not persisted,
        # so a transient progress indicator never replays as a stale line on the next history
        # load. No payload.type is set so the FE's persistent-message filter
        # (`!status || payload.type`) drops it once the turn settles. The settled (success)
        # emit stays chat:response and is persisted like any other event (fresh event id) —
        # unchanged from SA-2079 — no collision with the assistant message sharing message_id.
        is_processing = status == TaskStatus.PROCESSING
        generated_id = self._uuid_builder.create_uuid7()
        ui_metadata = UIMetadata(main_conv_id=conv_id)

        ui_payload = UIPayload(
            id=generated_id,
            message_id=message_id,
            content=content,
            type=None if is_processing else UIPayloadType.TOKEN_INFO,
            status=status,
        )
        ui_event = UIEvent(
            event_id=generated_id,
            correlation_id=generated_id,
            event_type=UIEventType.CHAT_THINKING if is_processing else UIEventType.CHAT_RESPONSE,
            conv_id=conv_id,
            metadata=ui_metadata,
            payload=ui_payload,
        )
        if is_processing:
            await self._push_only(conv_id, ui_event)
        else:
            await self.send_notification(key=conv_id, data=ui_event)

    async def _push_only(self, key: str, data: UIEvent) -> None:
        # Push without persisting — mirrors send_notification's serialization/logging but
        # skips the repo writes (used for the ephemeral chat:thinking token_info frame).
        payload = self._json_safe(asdict(data))
        await self._notifier.send_notification(key, payload, is_final=False)
        self._logger.info(
            f"Sent notification to Push Gateway with key: {key} and data: {payload}"
        )

    async def send_process_response_with_text_event(
        self, conv_id: str, ui_payload: UIPayload
    ) -> None:
        generated_id = self._uuid_builder.create_uuid7()
        ui_metadata = UIMetadata(main_conv_id=conv_id)
        ui_event = UIEvent(
            event_id=generated_id,
            event_type=UIEventType.PROCESS_RESPONSE,
            conv_id=conv_id,
            metadata=ui_metadata,
            payload=ui_payload,
            correlation_id=generated_id,
        )
        await self.send_notification(key=conv_id, data=ui_event)

    async def send_tool_form_event(self, conv_id: str, ui_payload: UIPayload) -> None:
        generated_id = self._uuid_builder.create_uuid7()
        ui_event = UIEvent(
            event_id=generated_id,
            event_type=UIEventType.PROCESS_RESPONSE,
            conv_id=conv_id,
            correlation_id=generated_id,
            payload=ui_payload,
        )
        ui_metadata = UIMetadata(
            main_conv_id=conv_id,
            action_context=UIActionReference(
                source_event_id=ui_event.event_id,
                source_conv_id=ui_event.conv_id,
            ),
        )
        ui_event.metadata = ui_metadata
        await self.send_notification(key=conv_id, data=ui_event)
    
    async def send_viewer_event(self, conv_id: str, ui_payload: UIPayload) -> None:
        generated_id = self._uuid_builder.create_uuid7()
        ui_event = UIEvent(
            event_id=generated_id,
            event_type=UIEventType.PROCESS_RESPONSE,
            conv_id=conv_id,
            correlation_id=generated_id,
            payload=ui_payload,
        )
        ui_metadata = UIMetadata(
            main_conv_id=conv_id,
            action_context=UIActionReference(
                source_event_id=ui_event.event_id,
                source_conv_id=ui_event.conv_id,
            ),
        )
        ui_event.metadata = ui_metadata
        await self.send_notification(key=conv_id, data=ui_event)

    async def send_thinking_event(self, conv_id: str, thinking_message: str, task_id: str = "") -> None:
        generated_id = self._uuid_builder.create_uuid7()
        ui_metadata = UIMetadata(main_conv_id=conv_id)

        ui_payload = UIPayload(
            id=generated_id,
            content=thinking_message,
            status=TaskStatus.SUCCESS,
            parent_id=task_id,
        )
        ui_event = UIEvent(
            event_id=generated_id,
            event_type=UIEventType.CHAT_THINKING,
            conv_id=conv_id,
            metadata=ui_metadata,
            payload=ui_payload,
            correlation_id=generated_id,
        )

        await self.send_notification(key=conv_id, data=ui_event)


    async def send_view_space_event(self, conv_id: str, record_id: str, content: str, parent_id: str, object_type_id: str = "BusinessPartner") -> None:
        generated_id = self._uuid_builder.create_uuid7()
        ui_metadata = UIMetadata(main_conv_id=conv_id)

        ui_payload = UIPayload(
            id=generated_id,
            type=UIPayloadType.VIEW_SPACE,
            content=content,
            status=TaskStatus.SUCCESS,
            parent_id=parent_id,
            object_type_id=object_type_id,
            record_id=record_id,
        )
        ui_event = UIEvent(
            event_id=generated_id,
            event_type=UIEventType.PROCESS_RESPONSE,
            conv_id=conv_id,
            metadata=ui_metadata,
            payload=ui_payload,
            correlation_id=generated_id,
        )

        await self.send_notification(key=conv_id, data=ui_event)

    @classmethod
    def _json_safe(cls, value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime) or hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_safe(item) for item in value]
        return value

    def _build_chat_toggle_event(self, conv_id: str, event_type: UIEventType) -> UIEvent:
        generated_id = self._uuid_builder.create_uuid7()
        return UIEvent(
            event_id=generated_id,
            event_type=event_type,
            correlation_id=generated_id,
            conv_id=conv_id,
        )

    @staticmethod
    def _normalize_text_payload_status(event: UIEvent) -> None:
        payload = event.payload
        if payload is None:
            return

        payload_type = getattr(payload.type, "value", payload.type)
        if payload_type == UIPayloadType.TEXT.value:
            payload.status = TaskStatus.SUCCESS
