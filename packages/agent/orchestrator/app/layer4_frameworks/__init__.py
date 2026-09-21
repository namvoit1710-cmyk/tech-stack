"""Infrastructure layer — master factory.

Provides ``create_infrastructure()`` which composes all sub-factories
into a single ``InfrastructureSet``.
"""

from __future__ import annotations

import logging as _logging
from dataclasses import dataclass
from typing import Optional, Protocol

from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from app.layer1_domain.interfaces.llm_provider_interface import ILLMProvider
from app.layer2_application.interfaces.permission_provider import IPermissionProvider
from app.layer2_application.interfaces.agent_registry_client import IAgentRegistryClient

from .ai import create_llm_provider
from .clients import create_agent_registry_client, create_workflow_client
from .security import create_permission_provider
import logging

logger = logging.getLogger(__name__)


class IMessageBrokerClient(Protocol):
    """Low-level message broker protocol (inlined from shared)."""
    def publish_to_topic(self, topic: str, message, key=None) -> None: ...
    def subscribe_to_topic(self, topic: str, callback) -> None: ...


class IDatabaseConnection(Protocol):
    """Database connection protocol (inlined from shared)."""
    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]: ...
    def execute_write(self, sql: str, params: tuple = ()) -> None: ...


@dataclass(frozen=True)
class InfrastructureSet:
    llm_provider: ILLMProvider
    agent_registry_client: IAgentRegistryClient
    workflow_client: object
    permission_provider: IPermissionProvider


def create_infrastructure(
    settings
) -> InfrastructureSet:
    """Compose all infrastructure sub-factories into one set."""
    # 5. AI / LLM
    llm_provider = create_llm_provider(settings)

    # 6. Agent registry
    agent_registry_client = create_agent_registry_client(settings)

    # 7. Workflow API
    workflow_client = create_workflow_client(settings)

    # 8. Security
    permission_provider = create_permission_provider(settings)

    return InfrastructureSet(
        llm_provider=llm_provider,
        agent_registry_client=agent_registry_client,
        workflow_client=workflow_client,
        permission_provider=permission_provider,
    )
