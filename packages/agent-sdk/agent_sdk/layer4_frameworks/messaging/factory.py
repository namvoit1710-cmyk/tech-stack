from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from agent_sdk.bootstrap import _read_string_setting, _resolve_infra_mode
from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)
from agent_sdk.layer4_frameworks.messaging.topic_utils import parse_topic_list

logger = logging.getLogger(__name__)


class _NoOpPushGatewayNotifier(PushGatewayNotifierProtocol):
    async def send_notification(
        self, key: str, data: Any, *, is_final: bool = False
    ) -> None:
        pass

    async def close(self) -> None:
        pass


def _read_bool_setting(settings: Any, name: str, *, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _read_int_setting(settings: Any, name: str, *, default: int) -> int:
    value = getattr(settings, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_consume_topics(
    settings: Any,
    *,
    multi_names: tuple[str, ...],
    fallback_names: tuple[str, ...],
    default: str,
) -> list[str]:
    for name in multi_names:
        topics = parse_topic_list(getattr(settings, name, []))
        if topics:
            return topics
    for name in fallback_names:
        topic = _read_string_setting(settings, name)
        if topic:
            return [topic]
    return [default]


def build_push_gateway_notifier(settings: Any) -> PushGatewayNotifierProtocol:
    infra_mode = _resolve_infra_mode(settings)
    push_url = _read_string_setting(settings, "PUSH_GATEWAY_URL")
    push_grpc_target = _read_string_setting(settings, "PUSH_GATEWAY_GRPC_TARGET")
    normalized_transport = _read_string_setting(
        settings,
        "PUSH_GATEWAY_TRANSPORT",
        default="grpc",
    ).lower()
    buffer_enabled = _read_bool_setting(
        settings,
        "PUSH_GATEWAY_BUFFER_ENABLED",
        default=True,
    )

    if infra_mode == "mock":
        from agent_sdk.layer4_frameworks.messaging.console_push_gateway_notifier import (
            ConsolePushGatewayNotifier,
        )

        return ConsolePushGatewayNotifier()

    if normalized_transport not in {"http", "grpc"}:
        raise ValueError(f"Unsupported push gateway transport: {normalized_transport}")

    notifier: PushGatewayNotifierProtocol
    if normalized_transport == "http" and push_url:
        from agent_sdk.layer4_frameworks.messaging.http_push_gateway_notifier import (
            HttpPushGatewayNotifier,
        )

        notifier = HttpPushGatewayNotifier(push_url)
    elif normalized_transport == "grpc" and push_grpc_target:
        from agent_sdk.layer4_frameworks.messaging.grpc_push_gateway_notifier import (
            GrpcPushGatewayNotifier,
        )

        notifier = GrpcPushGatewayNotifier(push_grpc_target)
    else:
        notifier = _NoOpPushGatewayNotifier()

    if not buffer_enabled or isinstance(notifier, _NoOpPushGatewayNotifier):
        return notifier

    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )

    return BufferedPushGatewayNotifier(
        inner=notifier,
        max_size=_read_int_setting(
            settings,
            "PUSH_GATEWAY_BUFFER_MAX_SIZE",
            default=256,
        ),
        enqueue_timeout_ms=_read_int_setting(
            settings,
            "PUSH_GATEWAY_BUFFER_ENQUEUE_TIMEOUT_MS",
            default=50,
        ),
        drain_timeout_seconds=_read_int_setting(
            settings,
            "PUSH_GATEWAY_BUFFER_DRAIN_TIMEOUT_SECONDS",
            default=5,
        ),
        logger=logger,
    )


@dataclass(frozen=True)
class MessagingSet:
    publisher: Any
    consumer: Any
    backend_name: str


def _build_mock_messaging(sdk_logger: Any) -> MessagingSet:
    from agent_sdk.layer4_frameworks.messaging.console_publisher import (
        ConsoleMessagePublisher,
    )
    from agent_sdk.layer4_frameworks.messaging.mock_consumer import MockMessageConsumer

    return MessagingSet(
        publisher=ConsoleMessagePublisher(),
        consumer=MockMessageConsumer(logger=sdk_logger),
        backend_name="mock",
    )


def _build_kafka_messaging(settings: Any, broker: Any, sdk_logger: Any) -> MessagingSet:
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )
    from agent_sdk.layer4_frameworks.messaging.kafka_publisher import (
        KafkaMessagePublisher,
    )

    topics = _resolve_consume_topics(
        settings,
        multi_names=("KAFKA_CONSUME_TOPICS", "QUEUE_CONSUME_TOPICS"),
        fallback_names=("KAFKA_REQUEST_TOPIC", "QUEUE_REQUEST_TOPIC"),
        default="agent.request",
    )
    return MessagingSet(
        publisher=KafkaMessagePublisher(broker_client=broker, logger=sdk_logger),
        consumer=KafkaMessageConsumer(
            broker_client=broker, topics=topics, logger=sdk_logger
        ),
        backend_name="kafka",
    )


def _build_event_mesh_messaging(
    settings: Any, broker: Any, sdk_logger: Any
) -> MessagingSet:
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )
    from agent_sdk.layer4_frameworks.messaging.event_mesh_publisher import (
        EventMeshMessagePublisher,
    )

    topics = _resolve_consume_topics(
        settings,
        multi_names=("EVENT_MESH_CONSUME_TOPICS", "QUEUE_CONSUME_TOPICS"),
        fallback_names=(
            "EVENT_MESH_REQUEST_TOPIC",
            "QUEUE_REQUEST_TOPIC",
            "KAFKA_REQUEST_TOPIC",
        ),
        default="agent.request",
    )
    queue_name = _read_string_setting(settings, "QUEUE_NAME") or None
    return MessagingSet(
        publisher=EventMeshMessagePublisher(broker_client=broker, logger=sdk_logger),
        consumer=EventMeshMessageConsumer(
            broker_client=broker,
            topics=topics,
            logger=sdk_logger,
            queue_name=queue_name,
        ),
        backend_name="event_mesh",
    )


def create_messaging(
    settings: Any,
    sdk_logger: Any,
    *,
    broker: Optional[Any] = None,
) -> MessagingSet:
    messaging_mode = _resolve_infra_mode(settings)

    if broker is None or messaging_mode == "mock":
        result = _build_mock_messaging(sdk_logger)
    elif messaging_mode == "sap":
        result = _build_event_mesh_messaging(settings, broker, sdk_logger)
    else:
        result = _build_kafka_messaging(settings, broker, sdk_logger)

    logger.info("Messaging backend: %s", result.backend_name)
    return result
