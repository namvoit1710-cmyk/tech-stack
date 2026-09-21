from __future__ import annotations

import asyncio
import json
import signal
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from typing import Any, TypedDict, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
from agent_sdk.layer1_domain.value_objects.agent_control import (
    AGENT_CANCEL_ACTION,
    extract_conv_id,
    is_agent_control_message,
    is_cancel_control_message,
)
from agent_sdk.layer1_domain.value_objects.message_type_set import MessageTypeSet
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentOutput,
)
from agent_sdk.layer2_application.services.message_deduplicator import (
    MessageDeduplicator,
)
from agent_sdk.layer2_application.services.message_reaction.default_handlers import (
    EXECUTE_MESSAGE_TYPES,
    build_execute_input,
    build_execute_response,
    build_resume_input,
)
from agent_sdk.layer2_application.services.message_type_settings import (
    configured_response_message_types_from_dependencies,
)
from agent_sdk.layer3_adapters.presenters.agent_ops import (
    attach_ops_routes,
    build_agent_info_payload,
    managed_agent_lifecycle,
)

INTERNAL_AGENT_ERROR_MESSAGE = "Internal agent error"


class _AgentCallInterruptPayload(TypedDict, total=False):
    type: str
    agent_id: str
    agent_type: str
    input: dict[str, Any] | str


class _TrackedRouterDelivery:
    def __init__(self, delivery: Any) -> None:
        self._delivery = delivery
        self.outcome: str | None = None

    @property
    def payload(self) -> Any:
        return getattr(self._delivery, "payload", self._delivery)

    async def ack(self) -> None:
        if hasattr(self._delivery, "ack"):
            await self._delivery.ack()
        self.outcome = "acked"

    async def nack(self, requeue: bool = True) -> None:
        if hasattr(self._delivery, "nack"):
            await self._delivery.nack(requeue=requeue)
        self.outcome = "nacked"

    async def reject(self) -> None:
        if hasattr(self._delivery, "reject"):
            await self._delivery.reject()
        self.outcome = "rejected"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delivery, name)


def _coerce_agent_call_payload(
    raw_payload: Any,
) -> _AgentCallInterruptPayload | None:
    if not isinstance(raw_payload, dict):
        return None

    payload_type = raw_payload.get("type")
    agent_id = raw_payload.get("agent_id")
    if payload_type != "AGENT_CALL" or not isinstance(agent_id, str) or not agent_id:
        return None

    return cast(_AgentCallInterruptPayload, raw_payload)


_DEFAULT_EXECUTE_REPLY_TOPIC = "agent.responses"
_DEFAULT_EXECUTE_RESPONSE_MESSAGE_TYPE = "agent.response"


def _clean_topic(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _clean_message_type(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _coerce_message_type_set(value: Any) -> set[str]:
    return MessageTypeSet.coerce(value)


def _consumer_topic_set(consumer: Any) -> set[str]:
    topics: set[str] = set()
    raw_topics = getattr(consumer, "_topics", None) or getattr(consumer, "topics", None)
    if isinstance(raw_topics, (list, tuple, set)):
        for topic in raw_topics:
            cleaned = _clean_topic(topic)
            if cleaned:
                topics.add(cleaned)

    for attr_name in ("_topic", "topic"):
        cleaned = _clean_topic(getattr(consumer, attr_name, ""))
        if cleaned:
            topics.add(cleaned)

    return topics


def _configured_reply_topic(dependencies: dict[str, Any]) -> str:
    settings = dependencies.get("settings")
    if settings is None:
        return ""

    for name in (
        "QUEUE_REPLY_TOPIC",
        "EVENT_MESH_REPLY_TOPIC",
        "KAFKA_RESPONSE_TOPIC",
        "KAFKA_REPLY_TOPIC",
    ):
        topic = _clean_topic(getattr(settings, name, ""))
        if topic:
            return topic

    return ""


def _configured_response_message_type(dependencies: dict[str, Any]) -> str:
    settings = dependencies.get("settings")
    if settings is None:
        return ""
    return _clean_message_type(getattr(settings, "QUEUE_RESPONSE_MESSAGE_TYPE", ""))


def _configured_response_message_types(dependencies: dict[str, Any]) -> set[str]:
    return configured_response_message_types_from_dependencies(
        dependencies,
        default=_DEFAULT_EXECUTE_RESPONSE_MESSAGE_TYPE,
    )


def _read_response_message_type(
    payload: dict[str, Any], dependencies: dict[str, Any]
) -> str:
    raw_input = payload.get("input")
    nested_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    execution_context = payload.get("execution_context")
    if not isinstance(execution_context, dict) and isinstance(nested_input, dict):
        execution_context = nested_input.get("execution_context")
    if not isinstance(execution_context, dict):
        execution_context = {}

    return (
        _clean_message_type(payload.get("response_message_type"))
        or _clean_message_type(payload.get("response_type"))
        or _clean_message_type(nested_input.get("response_message_type"))
        or _clean_message_type(nested_input.get("response_type"))
        or _clean_message_type(execution_context.get("response_message_type"))
        or _clean_message_type(execution_context.get("response_type"))
        or _configured_response_message_type(dependencies)
    )


def _apply_response_message_type(request: Any, response_message_type: str) -> None:
    if not response_message_type:
        return
    execution_context = getattr(request, "execution_context", None)
    if not isinstance(execution_context, dict):
        execution_context = {}
        setattr(request, "execution_context", execution_context)
    execution_context.setdefault("response_message_type", response_message_type)


def _log_unsafe_reply_topic(
    *,
    dependencies: dict[str, Any],
    topic: str,
    consumed_topics: set[str],
    source: str,
) -> None:
    logger = dependencies.get("logger")
    if logger is not None and hasattr(logger, "warning"):
        logger.warning(
            "Unsafe execute reply topic resolved to a consumed topic; response publish skipped to avoid a queue loop.",
            reply_topic=topic,
            consumed_topics=sorted(consumed_topics),
            reply_topic_source=source,
        )


def _resolve_consumer_reply_topic(dependencies: dict[str, Any]) -> str:
    """Resolve the topic used by downstream agents to answer this consumer.

    This is intentionally allowed to be a topic this process consumes, because
    queue-native supervisor flows need to consume downstream ``agent.response``
    messages to resume a paused parent thread.
    """
    return _configured_reply_topic(dependencies) or _DEFAULT_EXECUTE_REPLY_TOPIC


def _resolve_execute_reply_topic(
    payload: dict[str, Any],
    consumer: Any,
    dependencies: dict[str, Any],
) -> str:
    """Resolve where this agent should publish the final/error response.

    Never fall back to the current request topic. Publishing an ``agent.response``
    to a topic consumed by the same process can cause self-triggered reprocessing
    or endless nack/requeue loops, especially for error responses.
    """
    source = "default"
    reply_topic = ""

    if "reply_to" in payload or "reply_topic" in payload:
        reply_topic = _clean_topic(payload.get("reply_to")) or _clean_topic(
            payload.get("reply_topic")
        )
        source = "payload"

    settings = dependencies.get("settings")
    if (
        not reply_topic
        and payload.get("type") == "executor.request.agent"
        and settings is not None
    ):
        reply_topic = _clean_topic(getattr(settings, "EXECUTOR_STATUS_TOPIC", ""))
        source = "EXECUTOR_STATUS_TOPIC" if reply_topic else source

    if not reply_topic:
        reply_topic = _configured_reply_topic(dependencies)
        source = "configured" if reply_topic else source

    if not reply_topic:
        reply_topic = _DEFAULT_EXECUTE_REPLY_TOPIC
        source = "default"

    consumed_topics = _consumer_topic_set(consumer)
    if reply_topic in consumed_topics:
        _log_unsafe_reply_topic(
            dependencies=dependencies,
            topic=reply_topic,
            consumed_topics=consumed_topics,
            source=source,
        )
        return ""

    return reply_topic


def _coerce_agent_call_input(raw_input: Any) -> dict[str, Any]:
    if isinstance(raw_input, str):
        try:
            parsed = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"input_payload must be a dict, got invalid JSON: {exc}"
            ) from exc
    else:
        parsed = raw_input

    if not isinstance(parsed, dict):
        raise ValueError(f"input_payload must be a dict, got {type(parsed).__name__}")

    return parsed


def _resolve_shutdown_event(dependencies: dict[str, Any]) -> asyncio.Event:
    shutdown_event = dependencies.get("shutdown_event")
    if isinstance(shutdown_event, asyncio.Event):
        return shutdown_event
    return asyncio.Event()


def _install_shutdown_signal_handlers(
    shutdown_event: asyncio.Event,
    logger: Any,
) -> list[signal.Signals]:
    installed_handlers: list[signal.Signals] = []
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return installed_handlers

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, shutdown_event.set)
            installed_handlers.append(signum)
        except (NotImplementedError, RuntimeError, ValueError):
            if logger is not None and hasattr(logger, "debug"):
                logger.debug(
                    "Consumer shutdown signal handler not installed",
                    signum=getattr(signum, "name", str(signum)),
                )
    return installed_handlers


def _remove_shutdown_signal_handlers(installed_handlers: list[signal.Signals]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for signum in installed_handlers:
        loop.remove_signal_handler(signum)


@asynccontextmanager
async def _maybe_managed_agent_lifecycle(
    dependencies: dict[str, Any],
    *,
    enabled: bool,
):
    if enabled:
        async with managed_agent_lifecycle(dependencies):
            yield
    else:
        yield


def create_consumer_ops_app(container: dict) -> FastAPI:
    dependencies = container.get("_dependencies") or {}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        yield

    app = FastAPI(title="Agent SDK Consumer Ops", version="1.0.0", lifespan=_lifespan)
    settings = dependencies.get("settings")
    allow_origins = ["*"]
    if settings is not None:
        allow_origins = getattr(settings, "ALLOW_ORIGINS", ["*"])
    allow_credentials = False if not allow_origins or "*" in allow_origins else True
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.dependencies = dependencies
    attach_ops_routes(app, container)

    @app.get("/api/v1/info", tags=["ops"])
    def info():
        return build_agent_info_payload(container)

    return app


async def _serve_consumer_ops_app(
    container: dict, shutdown_event: asyncio.Event
) -> None:
    import uvicorn

    dependencies = container.get("_dependencies") or {}
    settings = dependencies.get("settings")
    if settings is None:
        return

    app = create_consumer_ops_app(container)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=getattr(settings, "SERVER_HOST", "0.0.0.0"),
            port=getattr(settings, "SERVER_PORT", 36000),
            log_level="warning",
        )
    )
    server_task = asyncio.create_task(server.serve())
    try:
        await shutdown_event.wait()
    finally:
        server.should_exit = True
        await server_task


def _consumer_ops_enabled(dependencies: dict[str, Any]) -> bool:
    settings = dependencies.get("settings")
    if settings is None:
        return False
    return bool(getattr(settings, "CONSUMER_OPS_ENABLED", False))


async def run_consumer_agent(
    container: dict,
    *,
    manage_lifecycle: bool = True,
    install_signal_handlers: bool = True,
    start_ops_app: bool = True,
) -> None:
    execute_use_case = container["execute_agent"]
    resume_use_case = container.get("resume_agent")
    consumer = container["consumer"]
    publisher = container["publisher"]
    router = container.get("message_reaction_router")
    logger = container.get("logger") or _NoopLogger()
    dependencies = container.get("_dependencies") or {}
    agent_delegator = container.get("agent_delegator") or dependencies.get(
        "agent_delegator"
    )
    consumer_reply_topic = _resolve_consumer_reply_topic(dependencies)
    consumer_reply_queue = getattr(consumer, "_queue_name", None)
    shutdown_event = _resolve_shutdown_event(dependencies)
    dedup = MessageDeduplicator()

    inbox_repository = container.get("inbox_repository") or dependencies.get(
        "inbox_repository"
    )
    if inbox_repository is not None:
        from agent_sdk.layer2_application.services.durable_message_deduplicator import (
            DurableMessageDeduplicator,
        )

        durable_dedup: DurableMessageDeduplicator | None = DurableMessageDeduplicator(
            inbox_repository
        )
    else:
        durable_dedup = None

    async def _handle(delivery: Any) -> None:
        broker_finalized = False

        async def _ack_inner(*, message_id: str = "") -> None:
            nonlocal broker_finalized
            if broker_finalized:
                return
            if message_id:
                dedup.mark_seen(message_id)
            if hasattr(delivery, "ack"):
                await delivery.ack()
            broker_finalized = True

        async def _nack_inner(*, requeue: bool = True) -> None:
            nonlocal broker_finalized
            if broker_finalized:
                return
            if hasattr(delivery, "nack"):
                await delivery.nack(requeue=requeue)
            broker_finalized = True

        async def _reject_inner() -> None:
            nonlocal broker_finalized
            if broker_finalized:
                return
            if hasattr(delivery, "reject"):
                await delivery.reject()
            broker_finalized = True

        raw = getattr(delivery, "payload", delivery)
        if not isinstance(raw, dict):
            await _reject_inner()
            return

        msg_id = raw.get("message_id") or ""
        if durable_dedup is not None:
            if msg_id and durable_dedup.is_duplicate(msg_id):
                logger.warning(
                    "Duplicate message skipped (durable)",
                    message_id=msg_id,
                )
                await _ack_inner()
                return
            if msg_id:
                try:
                    durable_dedup.mark_received(msg_id, msg_id, raw)
                except Exception:
                    logger.error(
                        "Inbox persistence failed, nacking for redelivery",
                        message_id=msg_id,
                        exc_info=True,
                    )
                    await _nack_inner(requeue=True)
                    return
        else:
            if msg_id and dedup.is_duplicate(msg_id):
                logger.warning(
                    "Duplicate message skipped",
                    message_id=msg_id,
                )
                await _ack_inner()
                return

        message_type = raw.get("type") or ""

        # SA-892 (N4): control-plane messages (e.g. a cooperative stop) are
        # handled inline here and never enter the execute/router path, so a stop
        # is not starved behind a running turn under max_in_flight=1. The broker
        # dispatches these via ``submit_control`` (no in-flight slot); the mock
        # consumer path lands here directly. We cancel by conv_id and ack.
        if is_agent_control_message(raw):
            conv_id = extract_conv_id(raw)
            cancelled = 0
            cancel_conversation = getattr(consumer, "cancel_conversation", None)
            if is_cancel_control_message(raw):
                if conv_id and callable(cancel_conversation):
                    cancelled = cancel_conversation(conv_id)
            else:
                # SA-892 (S1): the message IS a control message but NOT a
                # recognized action (only `cancel` is actioned today). Log a
                # WARNING so a future/unhandled control type is observable rather
                # than silently swallowed. We still ack (do not reject/requeue):
                # requeueing an action nobody handles would just hot-loop.
                logger.warning(
                    "Unrecognized agent control action; acking without action",
                    correlation_id=raw.get("correlation_id", ""),
                    conv_id=conv_id,
                    message_type=message_type,
                    action=raw.get("action", ""),
                )
            logger.info(
                "Agent control message handled",
                correlation_id=raw.get("correlation_id", ""),
                conv_id=conv_id,
                action=raw.get("action") or AGENT_CANCEL_ACTION,
                cancelled=cancelled,
            )
            if durable_dedup is not None and msg_id:
                durable_dedup.mark_completed(msg_id)
            await _ack_inner(message_id=msg_id)
            return

        if not message_type:
            logger.warning(
                "Inbound message has no 'type' field; treating as execute request",
                correlation_id=raw.get("correlation_id", ""),
            )
        if (
            router is not None
            and message_type
            and message_type not in EXECUTE_MESSAGE_TYPES
            and message_type != "agent.response"
        ):
            try:
                tracked_router_delivery = _TrackedRouterDelivery(delivery)
                await router.handle(tracked_router_delivery)
                if tracked_router_delivery.outcome == "acked":
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_completed(msg_id)
                    elif msg_id:
                        dedup.mark_seen(msg_id)
                    return
                if tracked_router_delivery.outcome == "rejected":
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_failed(
                            msg_id,
                            f"Router rejected message type: {message_type}",
                        )
                    return
                if tracked_router_delivery.outcome == "nacked":
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_failed(
                            msg_id,
                            f"Router nacked message type: {message_type}",
                        )
                    return
                logger.error(
                    "Message reaction router returned without finalizing delivery",
                    correlation_id=raw.get("correlation_id", ""),
                    message_type=message_type,
                )
                if durable_dedup is not None and msg_id:
                    durable_dedup.mark_failed(
                        msg_id,
                        "Message reaction router did not finalize delivery",
                    )
                await _nack_inner(requeue=True)
                return
            except Exception as exc:
                if tracked_router_delivery.outcome == "acked":
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_completed(msg_id)
                    elif msg_id:
                        dedup.mark_seen(msg_id)
                    logger.error(
                        "Router raised after ack; treating broker message as completed",
                        correlation_id=raw.get("correlation_id", ""),
                        message_type=message_type,
                        exc_info=True,
                    )
                    return
                if tracked_router_delivery.outcome in {"nacked", "rejected"}:
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_failed(msg_id, str(exc))
                    raise
                if durable_dedup is not None and msg_id:
                    durable_dedup.mark_failed(msg_id, str(exc))
                await _nack_inner(requeue=True)
                raise
        response_types = _configured_response_message_types(dependencies)
        if message_type and message_type not in EXECUTE_MESSAGE_TYPES | response_types:
            if durable_dedup is not None and msg_id:
                durable_dedup.mark_failed(
                    msg_id, f"Unsupported message type: {message_type}"
                )
            await _reject_inner()
            return
        if message_type in response_types:
            try:
                if resume_use_case is not None:
                    delegation = raw.get("delegation") or {}
                    if not isinstance(delegation, dict):
                        delegation = {}
                    thread_id = (
                        raw.get("thread_id")
                        or delegation.get("parent_thread_id")
                        or raw.get("parent_thread_id")
                        or ""
                    )
                    if not thread_id:
                        if durable_dedup is not None and msg_id:
                            durable_dedup.mark_failed(
                                msg_id, "Missing thread_id for agent.response"
                            )
                        await _reject_inner()
                        return
                    await resume_use_case.execute(
                        build_resume_input(raw, thread_id=thread_id)
                    )
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_completed(msg_id)
                    await _ack_inner(message_id=msg_id)
                    return
                else:
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_failed(
                            msg_id, "No resume_use_case for agent.response"
                        )
                    await _reject_inner()
                    return
            except Exception as exc:
                if durable_dedup is not None and msg_id:
                    durable_dedup.mark_failed(msg_id, str(exc))
                await _nack_inner(requeue=True)
                raise

        correlation_id = raw.get("correlation_id", "")
        reply_to = _resolve_execute_reply_topic(raw, consumer, dependencies)

        logger.info(
            "Consumer processing message",
            correlation_id=correlation_id,
            reply_topic=reply_to,
        )
        session_id = raw.get("session_id", "")
        request = cast(Any, build_execute_input(raw))
        _apply_response_message_type(
            request, _read_response_message_type(raw, dependencies)
        )
        try:
            output: ExecuteAgentOutput = await execute_use_case.execute(request)
        except Exception:
            logger.error(
                "Unhandled exception in execute",
                correlation_id=correlation_id,
                exc_info=True,
            )
            if durable_dedup is not None and msg_id:
                durable_dedup.mark_failed(msg_id, INTERNAL_AGENT_ERROR_MESSAGE)
            error_response = build_execute_response(
                ExecuteAgentOutput(
                    message=INTERNAL_AGENT_ERROR_MESSAGE,
                    status="error",
                    correlation_id=correlation_id,
                    session_id=session_id,
                    error=INTERNAL_AGENT_ERROR_MESSAGE,
                    error_code="INTERNAL_ERROR",
                ),
                request_correlation_id=request.correlation_id or correlation_id,
                parent_thread_id=(
                    (
                        (raw.get("delegation") or {})
                        if isinstance(raw.get("delegation"), dict)
                        else {}
                    ).get("parent_thread_id")
                    or raw.get("thread_id")
                ),
                request=request,
            )
            if reply_to:
                try:
                    await publisher.publish(
                        topic=reply_to, message=error_response, key=correlation_id
                    )
                except Exception:
                    logger.error(
                        "Failed to publish execute error response",
                        correlation_id=correlation_id,
                        exc_info=True,
                    )
            await _ack_inner(message_id=msg_id)
            return

        if output.interrupted and output.interrupt_payload:
            payload_val = _coerce_agent_call_payload(
                getattr(output.interrupt_payload, "value", output.interrupt_payload)
            )
            if payload_val is not None and agent_delegator is not None:
                call_request = replace(
                    AgentCallRequest(
                        agent_id=payload_val["agent_id"],
                        agent_type=str(payload_val.get("agent_type") or ""),
                        input_payload=_coerce_agent_call_input(
                            payload_val.get("input", {})
                        ),
                        interrupt_id=output.interrupt_payload.interrupt_id,
                        thread_id=request.conv_id
                        or getattr(output.interrupt_payload, "thread_id", None),
                    ),
                    correlation_id=request.correlation_id or output.correlation_id,
                    session_id=output.session_id or request.session_id,
                    reply_topic=consumer_reply_topic,
                    reply_queue=(
                        consumer_reply_queue
                        if isinstance(consumer_reply_queue, str)
                        else None
                    ),
                    metadata=asdict(request.metadata),
                    context_snapshot=dict(request.context_snapshot or {}),
                )
                try:
                    await agent_delegator.delegate(call_request)
                except Exception as exc:
                    if durable_dedup is not None and msg_id:
                        durable_dedup.mark_failed(msg_id, str(exc))
                    raise
                if durable_dedup is not None and msg_id:
                    durable_dedup.mark_completed(msg_id)
                await _ack_inner(message_id=msg_id)
                logger.info(
                    "Delegated AGENT_CALL to sub-agent via queue.",
                    correlation_id=correlation_id,
                )
                return

        response: dict = build_execute_response(
            output,
            request_correlation_id=request.correlation_id or "",
            parent_thread_id=(
                (
                    (raw.get("delegation") or {})
                    if isinstance(raw.get("delegation"), dict)
                    else {}
                ).get("parent_thread_id")
                or raw.get("thread_id")
            ),
            request=request,
        )
        if not reply_to:
            logger.warning(
                "Skipping execute response publish because no safe reply topic was resolved",
                correlation_id=correlation_id,
            )
            if durable_dedup is not None and msg_id:
                durable_dedup.mark_completed(msg_id)
            await _ack_inner(message_id=msg_id)
            return

        try:
            await publisher.publish(
                topic=reply_to, message=response, key=correlation_id or None
            )
        except Exception as exc:
            if durable_dedup is not None and msg_id:
                durable_dedup.mark_failed(msg_id, str(exc))
            raise
        if durable_dedup is not None and msg_id:
            durable_dedup.mark_completed(msg_id)
        await _ack_inner(message_id=msg_id)
        logger.info(
            "Consumer response published",
            correlation_id=correlation_id,
            reply_topic=reply_to,
            status=output.status,
        )

    logger.info("Consumer agent starting")

    outbox_flush_task = None
    if "outbox_repository" in (dependencies or {}) or container.get(
        "outbox_repository"
    ):
        from agent_sdk.layer2_application.services.outbox_publisher import (
            OutboxPublisher,
        )

        if isinstance(publisher, OutboxPublisher):
            await publisher.flush_pending()

            async def _outbox_flush_loop() -> None:
                flush_interval = getattr(
                    dependencies.get("settings"),
                    "OUTBOX_FLUSH_INTERVAL_SECONDS",
                    30,
                )
                while not shutdown_event.is_set():
                    try:
                        await asyncio.wait_for(
                            shutdown_event.wait(), timeout=flush_interval
                        )
                    except asyncio.TimeoutError:
                        pass
                    if shutdown_event.is_set():
                        break
                    try:
                        flushed = await publisher.flush_pending()
                        if flushed:
                            logger.info(f"Outbox flusher retried {flushed} messages")
                    except Exception:
                        logger.error("Outbox flush error", exc_info=True)

            outbox_flush_task = asyncio.create_task(_outbox_flush_loop())

    async with _maybe_managed_agent_lifecycle(
        dependencies,
        enabled=manage_lifecycle,
    ):
        await consumer.start(_handle)
        ops_task = None
        if start_ops_app and _consumer_ops_enabled(dependencies):
            ops_container = {
                "get_agent_info": container.get("get_agent_info"),
                "_dependencies": dependencies,
            }
            ops_task = asyncio.create_task(
                _serve_consumer_ops_app(ops_container, shutdown_event)
            )
        if getattr(consumer, "requires_shutdown_wait", False):
            installed_handlers = (
                _install_shutdown_signal_handlers(shutdown_event, logger)
                if install_signal_handlers
                else []
            )
            try:
                await shutdown_event.wait()
            finally:
                if installed_handlers:
                    _remove_shutdown_signal_handlers(installed_handlers)
                if outbox_flush_task is not None:
                    outbox_flush_task.cancel()
                    try:
                        await outbox_flush_task
                    except asyncio.CancelledError:
                        pass
                stop = getattr(consumer, "stop", None)
                if callable(stop):
                    await stop()
                if ops_task is not None:
                    await ops_task
    logger.info("Consumer agent stopped")


class _NoopLogger:
    def info(self, message: str, **kwargs):
        pass

    def error(self, message: str, **kwargs):
        pass

    def warning(self, message: str, **kwargs):
        pass

    def debug(self, message: str, **kwargs):
        pass
