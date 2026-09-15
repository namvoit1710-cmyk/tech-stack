"""Messaging infrastructure factory."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
from app.layer2_application.interfaces.callback_publisher_interface import ICallbackPublisher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MessagingSet:
    event_publisher: IEventPublisher
    callback_publisher: Optional[ICallbackPublisher]
    worker_registry_client: Optional[object]
    file_service_client: Optional[object]
    backend_name: str


def _build_file_service_client(settings: Any) -> Any:
    """Create file service client if FILE_SERVICE_URL is configured."""
    file_service_url = getattr(settings, "FILE_SERVICE_URL", "")
    if file_service_url:
        from app.layer4_frameworks.clients.http_file_service_client import HttpFileServiceClient
        return HttpFileServiceClient(file_service_url)
    return None


def _build_mock_messaging(settings: Any) -> MessagingSet:
    from app.layer4_frameworks.messaging.console_publisher import ConsoleEventPublisher

    callback_publisher = None
    agent_service_url = getattr(settings, "AGENT_SERVICE_URL", "")
    if agent_service_url:
        from app.layer4_frameworks.messaging.http_callback_publisher import HttpCallbackPublisher
        callback_publisher = HttpCallbackPublisher(agent_service_url)

    worker_registry_client = None
    worker_execution_url = getattr(settings, "WORKER_EXECUTION_URL", "")
    if worker_execution_url:
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient
        worker_registry_client = HttpWorkerRegistryClient(worker_execution_url)

    return MessagingSet(
        event_publisher=ConsoleEventPublisher(),
        callback_publisher=callback_publisher,
        worker_registry_client=worker_registry_client,
        file_service_client=_build_file_service_client(settings),
        backend_name="mock",
    )


def _build_kafka_messaging(settings: Any, broker: Any) -> MessagingSet:
    from app.layer4_frameworks.messaging.kafka_event_publisher import KafkaEventPublisher
    from app.layer4_frameworks.messaging.callback_publisher import CallbackPublisher

    callback_publisher = CallbackPublisher(broker)

    worker_registry_client = None
    worker_execution_url = getattr(settings, "WORKER_EXECUTION_URL", "")
    if worker_execution_url:
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient
        worker_registry_client = HttpWorkerRegistryClient(worker_execution_url)

    return MessagingSet(
        event_publisher=KafkaEventPublisher(broker),
        callback_publisher=callback_publisher,
        worker_registry_client=worker_registry_client,
        file_service_client=_build_file_service_client(settings),
        backend_name="kafka",
    )


def _build_event_mesh_messaging(settings: Any, broker: Any) -> MessagingSet:
    from app.layer4_frameworks.messaging.event_mesh_publisher import EventMeshEventPublisher

    worker_registry_client = None
    worker_execution_url = getattr(settings, "WORKER_EXECUTION_URL", "")
    if worker_execution_url:
        from app.layer4_frameworks.clients.http_worker_registry_client import HttpWorkerRegistryClient
        worker_registry_client = HttpWorkerRegistryClient(worker_execution_url)

    return MessagingSet(
        event_publisher=EventMeshEventPublisher(broker),
        callback_publisher=None,
        worker_registry_client=worker_registry_client,
        file_service_client=_build_file_service_client(settings),
        backend_name="event_mesh",
    )


def create_messaging(settings: Any, broker: Any=None) -> MessagingSet:
    """Factory: select and build all messaging adapters."""
    messaging_mode = (
        getattr(settings, "MESSAGING_MODE", "")
        or getattr(settings, "INFRA_MODE", "mock")
    ).lower()

    if messaging_mode == "local" and broker is not None:
        messaging = _build_kafka_messaging(settings, broker)
    elif messaging_mode in ("event_mesh", "sap") and broker is not None:
        messaging = _build_event_mesh_messaging(settings, broker)
    else:
        messaging = _build_mock_messaging(settings)

    # Wrap event publisher with task dispatcher (HTTP or gRPC) if configured
    transport_mode = getattr(settings, "TRANSPORT_MODE", "http").lower()

    if transport_mode == "grpc":
        grpc_target = getattr(settings, "WORKER_EXECUTION_GRPC_TARGET", "")
        if grpc_target:
            from app.layer4_frameworks.messaging.grpc_task_dispatcher import GrpcTaskDispatcher
            from app.layer4_frameworks.clients.grpc_worker_registry_client import GrpcWorkerRegistryClient
            # HTTP fallback URL — used when a gRPC call to the executor fails.
            # Falls back to HttpTaskDispatcher / HttpWorkerRegistryClient calls.
            http_fallback_url = getattr(settings, "WORKER_EXECUTION_URL", "") or None
            wrapped = GrpcTaskDispatcher(
                messaging.event_publisher,
                grpc_target,
                http_fallback_url=http_fallback_url,
            )
            messaging = MessagingSet(
                event_publisher=wrapped,
                callback_publisher=messaging.callback_publisher,
                worker_registry_client=GrpcWorkerRegistryClient(
                    grpc_target,
                    http_fallback_url=http_fallback_url,
                ),
                file_service_client=messaging.file_service_client,
                backend_name=messaging.backend_name,
            )
    elif transport_mode == "event_mesh":
        if broker is not None:
            from app.layer4_frameworks.messaging.event_mesh_task_dispatcher import EventMeshTaskDispatcher
            wrapped = EventMeshTaskDispatcher(messaging.event_publisher, broker)
            messaging = MessagingSet(
                event_publisher=wrapped,
                callback_publisher=messaging.callback_publisher,
                worker_registry_client=messaging.worker_registry_client,
                file_service_client=messaging.file_service_client,
                backend_name=messaging.backend_name,
            )
        else:
            logger.warning(
                "TRANSPORT_MODE=event_mesh requires a broker, but none is configured "
                "— task dispatch will not reach the executor"
            )
    else:
        worker_execution_url = getattr(settings, "WORKER_EXECUTION_URL", "")
        if worker_execution_url:
            from app.layer4_frameworks.messaging.http_task_dispatcher import HttpTaskDispatcher
            wrapped = HttpTaskDispatcher(messaging.event_publisher, worker_execution_url)
            messaging = MessagingSet(
                event_publisher=wrapped,
                callback_publisher=messaging.callback_publisher,
                worker_registry_client=messaging.worker_registry_client,
                file_service_client=messaging.file_service_client,
                backend_name=messaging.backend_name,
            )

    logger.info("Messaging backend: %s", messaging.backend_name)
    return messaging
