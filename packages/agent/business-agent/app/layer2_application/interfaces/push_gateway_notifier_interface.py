from abc import ABC, abstractmethod
from typing import List, Optional
from agent_sdk.layer1_domain.entities.agent_shared_state import Task, TaskStatus
from app.layer1_domain.entities.next_step_suggestion import NextStepSuggestion
from app.layer1_domain.entities.ui_event import UIEvent, UIPayload
from app.layer1_domain.entities.queue_event import QueueEvent


class IPushGatewayNotifier(ABC):

    @abstractmethod
    async def send_notification(self, key: str, data: UIEvent) -> None: ...

    @abstractmethod
    async def send_chat_disable_event(self, conv_id: str) -> None: ...

    @abstractmethod
    async def send_chat_enable_event(self, conv_id: str) -> None: ...

    @abstractmethod
    async def send_task_status_event(
        self, conv_id: str, task: Task
    ) -> None: ...

    @abstractmethod
    async def send_task_summary_event(
        self,
        conv_id: str,
        summary: str,
        suggestions: Optional[List[NextStepSuggestion]] = None,
    ) -> None: ...

    @abstractmethod
    async def send_process_response_with_text_event(
        self, conv_id: str, ui_payload: UIPayload
    ) -> None: ...

    @abstractmethod
    async def send_tool_form_event(
        self, conv_id: str, ui_payload: UIPayload
    ) -> None: ...

    @abstractmethod
    async def send_thinking_event(
        self, conv_id: str, thinking_message: str, task_id: str = ""
    ) -> None: ...

    @abstractmethod
    async def send_view_space_event(
        self, conv_id: str, record_id: str, content: str, parent_id: str, object_type_id: str = "BusinessPartner",
    ) -> None: ...

    @abstractmethod
    async def send_viewer_event(self, conv_id: str, ui_payload: UIPayload) -> None: ...

    @abstractmethod
    async def send_token_info_event(
        self, conv_id: str, message_id: str, content: str, status: TaskStatus
    ) -> None: ...
