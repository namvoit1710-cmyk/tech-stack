from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from agent_sdk.layer4_frameworks.logging.standard import StandardLogger

from app.layer2_application.features.dispatch_step.use_cases.dispatch_step_use_case import (
    DispatchStepUseCase,
)
from app.layer2_application.services.retry_policy import RetryPolicy
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.messaging.direct_step_status_publisher import (
    DirectStepStatusPublisher,
)
from app.layer4_frameworks.registry.http_capability_resolver import (
    HttpCapabilityResolver,
)
from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter
from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneClient,
)
from app.layer4_frameworks.tools.local_tool_executor import LocalToolExecutor
from app.layer4_frameworks.tools.tool_registry import build_local_tools


def build_tools(
    workflow_control_plane_client: WorkflowControlPlaneClient,
) -> list[BaseTool]:
    return [*build_local_tools(workflow_control_plane_client)]


def build_logger() -> StandardLogger:
    return StandardLogger()


def build_workflow_control_plane_client() -> WorkflowControlPlaneClient | None:
    if not settings.WORKFLOW_CONTROL_PLANE_URL:
        return None

    return WorkflowControlPlaneClient(
        base_url=settings.WORKFLOW_CONTROL_PLANE_URL,
        timeout=settings.WORKFLOW_CONTROL_PLANE_TIMEOUT_SECONDS,
    )


def build_status_publisher(publisher: Any) -> DirectStepStatusPublisher:
    return DirectStepStatusPublisher(
        publisher=publisher, topic=settings.STATUS_PUBLISH_TOPIC
    )


def build_agent_reply_waiter() -> AgentReplyWaiter:
    return AgentReplyWaiter()


def build_retry_policy(logger: Any = None) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.EXECUTOR_MAX_ATTEMPTS,
        backoff_base_seconds=settings.EXECUTOR_RETRY_BACKOFF_BASE_SECONDS,
        backoff_cap_seconds=settings.EXECUTOR_RETRY_BACKOFF_CAP_SECONDS,
        jitter=settings.EXECUTOR_RETRY_JITTER,
        logger=logger,
    )


def build_dependencies() -> dict:
    workflow_control_plane_client = build_workflow_control_plane_client()

    logger = build_logger()
    tools = build_tools(workflow_control_plane_client)
    tool_executor = LocalToolExecutor(
        tools=tools,
        logger_instance=logger,
        timeout_seconds=settings.EXECUTOR_LOCAL_TOOL_TIMEOUT_SECONDS,
    )
    capability_resolver = HttpCapabilityResolver(
        registry_url=settings.REGISTRY_URL,
    )
    agent_reply_waiter = build_agent_reply_waiter()
    retry_policy = build_retry_policy(logger)

    dispatcher = DispatchStepUseCase(
        capability_resolver=capability_resolver,
        tool_executor=tool_executor,
        logger=logger,
        retry_policy=retry_policy,
    )

    ioc_container: dict = {
        "settings": settings,
        "logger": logger,
        "workflow_control_plane_client": workflow_control_plane_client,
        "tool_executor": tool_executor,
        "capability_resolver": capability_resolver,
        "agent_reply_waiter": agent_reply_waiter,
        "retry_policy": retry_policy,
        "dispatch_step_use_case": dispatcher,
        "worker_tools": tools,
    }

    return ioc_container
