from typing import List, Optional, Protocol

from agent_sdk.layer1_domain.entities.agent_shared_state import GlobalContext, Task
from agent_sdk.layer2_application.interfaces.agent_shared_state_repository import (
    IAgentSharedStateRepository,
)


class IBusinessAgentSharedStateRepository(IAgentSharedStateRepository, Protocol):
    def get_conversation_id_by_run_id(self, run_id: str) -> Optional[str]: ...

    def get_context_by_conv_id(self, conv_id: str) -> Optional[GlobalContext]: ...

    def get_executions_by_conv_id(self, conv_id: str) -> List[Task]: ...

    def get_executions_by_message_id(
        self, conv_id: str, message_id: str
    ) -> List[Task]: ...