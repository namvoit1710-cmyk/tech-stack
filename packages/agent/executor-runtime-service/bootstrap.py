"""
Executor Runtime Service — Composition Root.

This is the ONLY place where infrastructure is instantiated and wired.
The executor does not use an agent graph (it is not a LangGraph agent).
It uses the SDK's CONSUMER mode with a custom_handler on MessageReactionRouter
to process agent.request.executor messages.
"""

from __future__ import annotations

import logging

from agent_sdk import build_app_container as _sdk_build_app_container
from agent_sdk.layer2_application.services.async_agent_delegator import (
    AsyncAgentDelegator,
)

from app.layer2_application.queues.executor_queue_handler import ExecutorQueueHandler
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.dependency.ioc_container import (
    build_dependencies,
    build_status_publisher,
)
from app.layer4_frameworks.messaging.technical_agent_reply_handler import (
    TechnicalAgentReplyHandler,
)
from app.layer4_frameworks.services.technical_agent_step_executor import (
    TechnicalAgentStepExecutor,
)
from app.layer2_application.features.dispatch_step.use_cases.dispatch_step_use_case import (
    DispatchStepUseCase,
)

logger = logging.getLogger(__name__)


def _build_message_reaction_handlers(
    handler: ExecutorQueueHandler,
) -> dict[str, object]:
    async def _handle_step_batch(payload: dict, _context: dict | None = None) -> None:
        await handler.handle_step_batch(payload)

    return {
        "agent.request.executor": _handle_step_batch,
    }


def build_app_container() -> dict:
    """
    Build the DI container for the Executor Runtime Service.

    Wiring:
        1. ioc_container builds tools explicitly with the workflow client,
            LocalToolExecutor,
         HttpCapabilityResolver, and DispatchStepUseCase
      2. ExecutorQueueHandler orchestrates dispatch + status publishing
    3. SDK container with custom_handler for agent.request.executor
    """
    logger.info("Bootstrapping %s...", settings.APP_NAME)

    deps = build_dependencies()
    reply_waiter = deps["agent_reply_waiter"]
    reply_handler = TechnicalAgentReplyHandler(reply_waiter, deps["logger"])
    message_reaction_handlers: dict[str, object] = {
        "agent.step.status": reply_handler,
    }

    container = _sdk_build_app_container(
        extra_dependencies={
            "settings": settings,
            "message_reaction_handlers": message_reaction_handlers,
            **deps,
        },
        local_tools=deps["worker_tools"],
    )

    dependencies = container.get("_dependencies") or deps
    publisher = container.get("publisher")
    if publisher is None:
        raise RuntimeError("SDK publisher is required to build executor runtime")

    agent_registry = dependencies.get("agent_registry")
    if agent_registry is None:
        raise RuntimeError("SDK agent_registry is required to build executor runtime")

    status_publisher = build_status_publisher(publisher)
    agent_delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=agent_registry,
        correlation_threads=dependencies.get("correlation_threads") or {},
        correlation_thread_store=dependencies.get("correlation_thread_store"),
        compatibility_request_topic=settings.AGENT_REQUEST_COMPATIBILITY_TOPIC,
        default_request_message_type=settings.DELEGATION_REQUEST_MESSAGE_TYPE,
        default_response_message_type=settings.DELEGATION_RESPONSE_MESSAGE_TYPE,
    )
    technical_agent_step_executor = TechnicalAgentStepExecutor(
        delegator=agent_delegator,
        reply_waiter=reply_waiter,
        reply_topic=settings.EXECUTOR_AGENT_REPLY_TOPIC,
        timeout_seconds=settings.EXECUTOR_AGENT_REPLY_TIMEOUT_SECONDS,
        logger=dependencies["logger"],
        retry_delegated_on_timeout=settings.EXECUTOR_RETRY_DELEGATED_ON_TIMEOUT,
    )
    dispatcher = DispatchStepUseCase(
        capability_resolver=dependencies["capability_resolver"],
        tool_executor=dependencies["tool_executor"],
        technical_agent_step_executor=technical_agent_step_executor,
        logger=dependencies["logger"],
        retry_policy=dependencies.get("retry_policy"),
    )
    handler = ExecutorQueueHandler(
        dispatcher=dispatcher,
        status_publisher=status_publisher,
        logger=dependencies["logger"],
    )
    message_reaction_handlers.update(_build_message_reaction_handlers(handler))
    dependencies["agent_delegator"] = agent_delegator
    dependencies["technical_agent_step_executor"] = technical_agent_step_executor
    dependencies["dispatch_step_use_case"] = dispatcher
    dependencies["status_publisher"] = status_publisher
    dependencies["executor_queue_handler"] = handler
    dependencies["message_reaction_handlers"] = message_reaction_handlers
    container["status_publisher"] = status_publisher
    container["agent_delegator"] = agent_delegator
    container["technical_agent_step_executor"] = technical_agent_step_executor
    container["dispatch_step_use_case"] = dispatcher
    container["executor_queue_handler"] = handler
    container["message_reaction_handlers"] = message_reaction_handlers

    router = container.get("message_reaction_router")
    if router is not None and hasattr(router, "_custom_handlers"):
        router._custom_handlers.update(message_reaction_handlers)

    logger.info(
        "%s bootstrapped: mode=%s topic=%s",
        settings.APP_NAME,
        settings.APP_MODE,
        settings.QUEUE_REQUEST_TOPIC,
    )

    return container
