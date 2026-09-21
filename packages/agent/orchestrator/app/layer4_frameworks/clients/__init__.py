"""Infrastructure client factories."""

from __future__ import annotations

import logging
from typing import Optional

from app.layer2_application.interfaces.agent_registry_client import IAgentRegistryClient

logger = logging.getLogger(__name__)


def create_agent_registry_client(
    settings,
) -> IAgentRegistryClient:
    """Factory: build the agent registry client."""
    infra_mode = getattr(settings, "INFRA_MODE", "mock").lower()

    if infra_mode != "mock":
        from app.layer4_frameworks.clients.agent_registry_client import AgentRegistryClient

        base_url = getattr(settings, "AGENT_REGISTRY_URL", "http://localhost:8003")
        client = AgentRegistryClient(base_url=base_url, logger=logger)
        logger.info("Agent registry client: registry (%s)", base_url)
    else:
        from app.layer4_frameworks.clients.mock_registry_client import MockRegistryClient
        client = MockRegistryClient()
        logger.info("Agent registry client: mock")

    return client


def create_workflow_client(
    settings,
):
    """Factory: build the workflow API client."""
    base_url = getattr(settings, "WORKFLOW_API_BASE_URL", "")
    if not base_url:
        from app.layer4_frameworks.clients.mock_workflow_client import MockWorkflowClient

        client = MockWorkflowClient()
        logger.info("Workflow client: mock")
        return client

    from app.layer4_frameworks.clients.workflow_client import WorkflowClient

    # 150 > the control plane's own 120 s wait in patch_node_data, deliberately.
    # Both sides used to sit on 30.0; the client then timed out at the same
    # instant as the server and every slow PATCH surfaced as an opaque
    # ReadTimeout instead of whatever the control plane actually said.
    client = WorkflowClient(base_url=base_url, timeout=150.0)
    logger.info("Workflow client: %s", base_url)
    return client
