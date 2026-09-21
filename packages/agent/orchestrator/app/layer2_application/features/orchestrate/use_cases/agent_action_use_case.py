"""
Orchestrate Use Case — routes user requests through the LangGraph state machine.
"""

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

import httpx
from app.layer1_domain.interfaces.logger_interface import ILogger
from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
from app.layer1_domain.entities.orchestrator.ui_event import UIActionReference, UIEvent, UIEventRole, UIEventType, UIMetadata, UIPayload, UIPayloadType, UIWFInfo, UIEventStatus
from app.layer1_domain.entities.orchestrator.queue_event import QUEUE_SOURCE, QUEUE_TARGET, NodeInfo, QueueEvent, QueuePayload, QueueRequestType
from app.layer2_application.interfaces.agent_event_repository import IAgentEventRepository
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder
from app.layer2_application.interfaces.workflow_client_interface import IWorkflowClient
from agent_sdk.layer2_application.interfaces.message_publisher import IMessagePublisher
from agent_sdk.layer2_application.interfaces.push_gateway_notifier import PushGatewayNotifierProtocol

SUCCESS_SUBMIT_THINKING_TEXT = "Update applied. Continuing processing..."

# Push-gateway delivery of the "Processing..." hint can hit a cold start (10-15s).
# Give each attempt a generous deadline and retry, since agent-action is a
# low-frequency sender whose connection is usually cold. Best-effort only.
PROCESSING_EVENT_SEND_TIMEOUT_SEC = 30.0
PROCESSING_EVENT_SEND_ATTEMPTS = 3


@dataclass
class WfInfo():
    run_id: str
    node_id: str
    task_id: Optional[str] = None


@dataclass
class AgentActionInput:
    wf_info: Optional[WfInfo] = None
    action: str = field(default="save", metadata={"description": "save | button | workspace_submit"})
    values: Dict[str, Any] = field(default_factory=dict)
    button_label: Optional[str] = None
    form_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "ui"


@dataclass
class AgentActionOutput:
    run_id: str = ""
    node_id: str = ""
    task_id: Optional[str] = None
    

class AgentActionUseCase:
    def __init__(
        self,
        logger: ILogger,
        publisher: IMessagePublisher,
        uuid_builder: IUuidBuilder,
        workflow_client: IWorkflowClient,
        agent_event_repo: IAgentEventRepository,
        push_gateway_notifier: PushGatewayNotifierProtocol | None = None,
        conversation_run_index: Any = None,
    ):
        self.logger = logger
        self.publisher = publisher
        self.uuid_builder = uuid_builder
        self.workflow_client = workflow_client
        self.agent_event_repo = agent_event_repo
        self.push_gateway_notifier = push_gateway_notifier
        # SA-892 (N10): record run ids observed here into the orchestrator's
        # conv_id -> set[run_id] index so a later stop can fan out to them.
        self.conversation_run_index = conversation_run_index


    async def execute_fire_and_forget(
        self, request: AgentActionInput,
    ) -> AgentActionOutput:
        # node_id = request.wf_info.node_id if request.wf_info else ""
        # run_id = request.wf_info.run_id if request.wf_info else ""

        # if not node_id or not run_id:
        #     self.logger.error("Missing node_id or run_id in AgentActionInput", node_id=node_id, run_id=run_id)
        #     raise ValueError("node_id and run_id are required in wf_info")
      
        # await self.publisher.publish(AGENT_REQUEST_TOPIC, message=asdict(request))

        # self.logger.info(
        #     "Published sub-conversation action to agent request topic",
        #     node_id=node_id,
        #     run_id=run_id,
        #     action=request.action,
        # )

        if request.wf_info is None:
            self.logger.error("Missing wf_info in AgentActionInput", action=request.action)
            raise ValueError("wf_info is required in AgentActionInput for fire-and-forget execution")

        artifacts = self._get_user_action_artifacts(request.form_data)
        action_reference = self._get_action_reference(request.metadata)
        source_event = self._get_source_event(action_reference)

        # SA-892 (N10): observe (conv_id, run_id) for the stop fan-out index.
        self._record_run_observation(action_reference, request.wf_info)

        try:
            if request.wf_info:
                await self._submit_user_action(request.wf_info, artifacts)
                if source_event is not None:
                    event = self._build_action_result_event(
                        source_event,
                        status=UIEventStatus.SUCCESS,
                        selected_label=request.button_label,
                    )
                    self.agent_event_repo.save_event(event)
                    self.agent_event_repo.upsert_timeline_event(event)

                    # Surface a "Processing..." thinking indicator, grouped with the form
                    # (reuses the form's payload id / parent_id / wf_info) and delivered on
                    # the main conversation socket the UI is subscribed to (SA-1283).
                    processing_event = self._build_processing_event(source_event)
                    await self._send_processing_event_to_frontend(processing_event)
        except Exception:
            if source_event is not None:
                event = self._build_action_result_event(
                    source_event,
                    status=UIEventStatus.ERROR,
                    selected_label=request.button_label,
                )
                self.agent_event_repo.save_event(event)
                self.agent_event_repo.upsert_timeline_event(event)
            raise

        return AgentActionOutput(
            run_id=request.wf_info.run_id if request.wf_info else "",
            node_id=request.wf_info.node_id if request.wf_info else "",
            task_id=request.wf_info.task_id if request.wf_info else None,
        )

    def _record_run_observation(
        self, action_reference: "UIActionReference | None", wf_info: "WfInfo | None"
    ) -> None:
        """Best-effort: record the run id seen for this conversation.

        Needs both a conversation (``action_context.source_conv_id``) and a
        ``wf_info.run_id``. A missing index or a failing write never breaks the
        action — the error is logged, never silently swallowed.
        """
        index = self.conversation_run_index
        if index is None or action_reference is None or wf_info is None:
            return
        run_id = getattr(wf_info, "run_id", "") or ""
        conv_id = getattr(action_reference, "source_conv_id", "") or ""
        if not run_id or not conv_id:
            return
        try:
            index.record(conv_id, run_id)
        except Exception as exc:  # best-effort: record, never break the action
            self.logger.warning(
                "Run index record failed", conv_id=conv_id, run_id=run_id, error=str(exc)
            )

    def _build_processing_event(self, source_event: AgentEvent) -> UIEvent:
        generated_id = self.uuid_builder.create_uuid7()
        source_payload = source_event.payload or {}
        payload_id = source_payload.get("id")
        if not isinstance(payload_id, str) or not payload_id:
            raise ValueError("action_context.payload.id is required")

        source_metadata = source_event.metadata if isinstance(source_event.metadata, dict) else {}
        metadata = UIMetadata(
            main_conv_id=self._string_value(source_metadata.get("main_conv_id")) or source_event.conv_id,
            sub_conv_ids=self._string_list(source_metadata.get("sub_conv_ids")),
            uploaded_file_ids=self._string_list(source_metadata.get("uploaded_file_ids")),
        )

        payload = UIPayload(
            id=payload_id,
            parent_id=self._optional_string(source_payload.get("parent_id")),
            type=UIPayloadType.TEXT,
            content=SUCCESS_SUBMIT_THINKING_TEXT,
            status=UIEventStatus.SUCCESS.value,
            sub_conv_id=None,  # self._optional_string(source_payload.get("sub_conv_id")),
            text=[SUCCESS_SUBMIT_THINKING_TEXT],
            wf_info=self._wf_info_from_dict(source_payload.get("wf_info")),
        )

        return UIEvent(
            event_id=generated_id,
            role=UIEventRole.ASSISTANT,
            event_type=UIEventType.CHAT_THINKING,
            correlation_id=generated_id,
            conv_id=source_event.conv_id,
            metadata=metadata,
            payload=payload,
        )

    async def _send_processing_event_to_frontend(self, event: UIEvent) -> None:
        if self.push_gateway_notifier is None:
            self.logger.warning("Skipping processing event push: no push_gateway_notifier wired", event_id=event.event_id)
            return

        conv_id = event.conv_id
        if not conv_id:
            self.logger.warning("Skipping processing event push: missing conv_id", event_id=event.event_id)
            return

        payload = self._json_safe(asdict(event))
        # The push gateway can cold-start (10-15s) while its own client timeout is short,
        # so a single send is unreliable for a low-frequency sender like agent-action.
        # Retry a few times with a generous per-attempt deadline. This is a best-effort
        # UI hint: it must never block or fail the action, so all errors are swallowed.
        last_error: str | None = None
        for attempt in range(1, PROCESSING_EVENT_SEND_ATTEMPTS + 1):
            try:
                await asyncio.wait_for(
                    self.push_gateway_notifier.send_notification(
                        key=conv_id, data=payload, is_final=False
                    ),
                    timeout=PROCESSING_EVENT_SEND_TIMEOUT_SEC,
                )
                self.logger.info(
                    "Sent processing event to frontend",
                    event_id=event.event_id,
                    conv_id=conv_id,
                    attempt=attempt,
                    notifier=type(self.push_gateway_notifier).__name__,
                )
                return
            except Exception as exc:  # noqa: BLE001 — UI hint is best-effort; never block the action
                last_error = str(exc)
                self.logger.warning(
                    "Processing event send attempt failed",
                    event_id=event.event_id,
                    conv_id=conv_id,
                    attempt=attempt,
                    error=last_error,
                )
        self.logger.warning(
            "Gave up sending processing event to frontend",
            event_id=event.event_id,
            conv_id=conv_id,
            attempts=PROCESSING_EVENT_SEND_ATTEMPTS,
            error=last_error,
        )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime) or hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: AgentActionUseCase._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [AgentActionUseCase._json_safe(item) for item in value]
        return value


    def _get_user_action_artifacts(
        self, form_data: Any
    ) -> list[dict[str, Any]] | None:
        if not isinstance(form_data, dict):
            return None

        raw_artifacts = form_data.get("artifacts")
        if isinstance(raw_artifacts, list):
            return raw_artifacts

        raw_values = form_data.get("values")
        values = raw_values if isinstance(raw_values, dict) else form_data
        if not values:
            return None

        return [
            {
                "artifact_id": "__root__",
                "operations": [
                    {"operation": "insert", "row_id": None, "data": values}
                ],
            }
        ]


    def _get_action_reference(self, metadata: Dict[str, Any]) -> UIActionReference | None:
        if not isinstance(metadata, dict):
            return None

        action_context_data = metadata.get("action_context")
        if action_context_data is None:
            return None
        if not isinstance(action_context_data, dict):
            raise ValueError("action_context must be an object")

        source_event_id = action_context_data.get("source_event_id")
        if not isinstance(source_event_id, str) or not source_event_id:
            raise ValueError("action_context.source_event_id is required")

        source_conv_id = action_context_data.get("source_conv_id")
        if not isinstance(source_conv_id, str) or not source_conv_id:
            raise ValueError("action_context.source_conv_id is required")

        return UIActionReference(
            source_event_id=source_event_id,
            source_conv_id=source_conv_id,
        )

    def _get_source_event(self, action_reference: UIActionReference | None) -> AgentEvent | None:
        if action_reference is None:
            return None
        event = self.agent_event_repo.get_event_by_id(action_reference.source_event_id)
        if event is None:
            raise ValueError("source event not found")
        if event.conv_id != action_reference.source_conv_id:
            raise ValueError("source event conversation mismatch")
        return event

    def _build_action_result_event(
        self,
        source_event: AgentEvent,
        *,
        status: UIEventStatus,
        selected_label: str | None = None,
    ) -> UIEvent:
        generated_id = self.uuid_builder.create_uuid7()
        source_payload = source_event.payload or {}
        payload_id = source_payload.get("id")
        if not isinstance(payload_id, str) or not payload_id:
            raise ValueError("action_context.payload.id is required")
        payload_type = self._payload_type(source_payload.get("type"))

        payload = UIPayload(
            id=payload_id,
            parent_id=self._optional_string(source_payload.get("parent_id")),
            type=payload_type,
            content=self._string_value(source_payload.get("content")),
            status=status.value,
            sub_conv_id=self._optional_string(source_payload.get("sub_conv_id")),
            text=self._payload_text(
                payload_type=payload_type,
                existing_text=self._string_list(source_payload.get("text")) or None,
                selected_label=selected_label,
            ),
            wf_info=self._wf_info_from_dict(source_payload.get("wf_info")),
        )

        source_metadata = source_event.metadata if isinstance(source_event.metadata, dict) else {}
        metadata = UIMetadata(
            main_conv_id=self._string_value(source_metadata.get("main_conv_id")) or source_event.conv_id,
            sub_conv_ids=self._string_list(source_metadata.get("sub_conv_ids")),
            uploaded_file_ids=self._string_list(source_metadata.get("uploaded_file_ids")),
        )

        return UIEvent(
            event_id=generated_id,
            role=UIEventRole.ASSISTANT,
            event_type=UIEventType.PROCESS_RESPONSE,
            correlation_id=generated_id,
            conv_id=source_event.conv_id,
            metadata=metadata,
            payload=payload,
        )

    @staticmethod
    def _payload_text(
        payload_type: UIPayloadType | None,
        existing_text: list[str] | None,
        selected_label: str | None,
    ) -> list[str] | None:
        if payload_type == UIPayloadType.BUTTON_GROUP and isinstance(selected_label, str) and selected_label:
            return [selected_label]
        if isinstance(existing_text, list):
            return [str(item) for item in existing_text if str(item)]
        return None

    @staticmethod
    def _payload_type(value: Any) -> UIPayloadType | None:
        if isinstance(value, UIPayloadType):
            return value
        if not isinstance(value, str) or not value:
            return None
        try:
            return UIPayloadType(value)
        except ValueError:
            return None

    @staticmethod
    def _string_value(value: Any) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item)]

    def _wf_info_from_dict(self, value: Any) -> UIWFInfo | None:
        payload_data = value if isinstance(value, dict) else None
        if payload_data is None:
            return None
        raw_wf_info = payload_data.get("wf_info")
        if raw_wf_info is None:
            raw_wf_info = payload_data
        if not isinstance(raw_wf_info, dict):
            return None
        return UIWFInfo(
            node_id=self._string_value(raw_wf_info.get("node_id")),
            task_id=self._string_value(raw_wf_info.get("task_id")),
            run_id=self._string_value(raw_wf_info.get("run_id")),
        )


    async def _submit_user_action(
        self, wf_info: WfInfo, artifacts: list[dict[str, Any]] | None
    ) -> None:
        if artifacts and all(
            hasattr(self.workflow_client, method)
            for method in ("get_data", "patch_data", "submit")
        ):
            _, etag = await self.workflow_client.get_data(
                wf_info.run_id,
                wf_info.node_id,
                task_id=wf_info.task_id,
            )
            await self.workflow_client.patch_data(
                wf_info.run_id,
                wf_info.node_id,
                artifacts,
                if_match=etag or "",
                task_id=wf_info.task_id,
            )
            await self.workflow_client.submit(
                wf_info.run_id,
                wf_info.node_id,
                task_id=wf_info.task_id,
            )
            self.logger.info("Fetched existing data and etag for user action submission", run_id=wf_info.run_id, node_id=wf_info.node_id, task_id=wf_info.task_id, etag=etag)
            return None

        if not artifacts and hasattr(self.workflow_client, "submit"):
            await self.workflow_client.submit(
                wf_info.run_id,
                wf_info.node_id,
                task_id=wf_info.task_id,
            )
            self.logger.info("Submitted user action without artifacts", run_id=wf_info.run_id, node_id=wf_info.node_id, task_id=wf_info.task_id)
            return None

        params = {"task_id": wf_info.task_id} if wf_info.task_id else None
        await self.workflow_client.submit_user_action(
            run_id=wf_info.run_id,
            node_id=wf_info.node_id,
            task_id=wf_info.task_id,
            artifacts=artifacts,
            params=params,
        )
        self.logger.info("Submitted user action with artifacts", run_id=wf_info.run_id, node_id=wf_info.node_id, task_id=wf_info.task_id, artifacts=artifacts)
        
