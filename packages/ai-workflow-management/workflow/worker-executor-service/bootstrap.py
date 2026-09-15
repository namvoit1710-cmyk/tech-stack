import glob
import importlib
import os
import inspect
from typing import Dict, Any

from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.logger.app_logger import StandardLogger
from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor
from app.layer4_frameworks.messaging.console_task_queue import ConsoleTaskQueue
from app.layer4_frameworks.messaging.console_result_publisher import ConsoleResultPublisher
from app.layer4_frameworks.providers.mock_decision_switch_client import MockDecisionSwitchClient
from app.layer4_frameworks.providers.worker_dispatcher import WorkerDispatcher
from app.layer4_frameworks.providers.http_worker_executor import HttpWorkerExecutor
from app.layer4_frameworks.providers.http_result_reporter import HttpResultReporter
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore
from app.layer4_frameworks.monitoring.trace_store import TaskTraceStore
from app.layer1_domain.entities.worker_registration import WorkerRegistration, WorkerCapability
from app.layer2_application.services.concurrency_control import ConcurrencyControl


def _create_message_broker_client(cfg):
    """Create a message broker client based on config, or return None for mock mode."""
    messaging_mode = (cfg.MESSAGING_MODE or cfg.INFRA_MODE).lower()
    if messaging_mode == "mock":
        return None

    if messaging_mode == "local":
        try:
            from app.layer4_frameworks.messaging.kafka_broker_client import (
                KafkaBrokerClient,
            )

            client = KafkaBrokerClient(
                bootstrap_servers=cfg.KAFKA_BOOTSTRAP_SERVERS,
                group_id=cfg.KAFKA_GROUP_ID,
                auto_offset_reset=cfg.KAFKA_AUTO_OFFSET_RESET,
            )
            print("   Message broker: Kafka (local mode)")
            return client
        except ImportError:
            print("[WARNING] confluent-kafka not installed, falling back to mock messaging")
            return None

    if messaging_mode == "sap":
        # Fail fast on missing credentials so we don't construct a client that
        # will later raise httpx.UnsupportedProtocol inside _refresh_token.
        missing = [
            name for name, value in (
                ("EVENT_MESH_TOKEN_URL", cfg.EVENT_MESH_TOKEN_URL),
                ("EVENT_MESH_MESSAGING_URL", cfg.EVENT_MESH_MESSAGING_URL),
                ("EVENT_MESH_CLIENT_ID", cfg.EVENT_MESH_CLIENT_ID),
                ("EVENT_MESH_CLIENT_SECRET", cfg.EVENT_MESH_CLIENT_SECRET),
            ) if not value
        ]
        if missing:
            print(
                f"[WARNING] Event Mesh credentials missing ({', '.join(missing)}); "
                f"check VCAP_SERVICES binding or env vars. Falling back to mock messaging."
            )
            return None

        try:
            from app.layer4_frameworks.messaging.event_mesh_broker_client import (
                EventMeshBrokerClient,
            )
            from app.layer4_frameworks.messaging.event_mesh_config import (
                EventMeshConfig,
            )

            client = EventMeshBrokerClient(EventMeshConfig(
                token_url=cfg.EVENT_MESH_TOKEN_URL,
                messaging_url=cfg.EVENT_MESH_MESSAGING_URL,
                management_url=cfg.EVENT_MESH_MANAGEMENT_URL,
                client_id=cfg.EVENT_MESH_CLIENT_ID,
                client_secret=cfg.EVENT_MESH_CLIENT_SECRET,
                namespace=getattr(cfg, "EVENT_MESH_NAMESPACE", "default"),
                poll_batch_size=getattr(cfg, "BROKER_POLL_BATCH_SIZE", 50),
                poll_idle_sleep_s=getattr(cfg, "BROKER_POLL_IDLE_SLEEP_SECONDS", 0.5),
                consumer_threads=getattr(cfg, "BROKER_CONSUMER_THREADS", 4),
                lock_duration_ms=getattr(cfg, "BROKER_LOCK_DURATION_MS", 60000),
                max_redelivery_count=getattr(cfg, "BROKER_MAX_REDELIVERY_COUNT", 50),
                consume_transport=getattr(cfg, "EVENT_MESH_CONSUME_TRANSPORT", "amqp"),
                amqp_url=getattr(cfg, "EVENT_MESH_AMQP_URL", ""),
                amqp_ws_path=getattr(cfg, "EVENT_MESH_AMQP_WS_PATH", "/protocols/amqp10ws"),
                amqp_prefetch=getattr(cfg, "EVENT_MESH_AMQP_PREFETCH", 10),
                amqp_concurrency=getattr(cfg, "EVENT_MESH_AMQP_CONCURRENCY", 0),
                amqp_strip_namespace=str(
                    getattr(cfg, "EVENT_MESH_AMQP_STRIP_NAMESPACE", "0")
                ).lower() not in ("0", "false", "no", ""),
            ))
            print("   Message broker: SAP Event Mesh (sap mode)")
            return client
        except Exception as exc:
            print(f"[WARNING] Failed to create Event Mesh broker: {exc}, falling back to mock")
            return None

    return None


def scan_and_load_features() -> Dict[str, Any]:
    """
    Scans app/layer2_application/features/*/use_cases/*_usecase.py for use case classes.
    Each file must export a PascalCase UseCase class (e.g. RegisterWorkerUseCase).
    Returns: { "feature_name": UseCaseClass }

    Convention:
      - File:  features/{feature_name}/use_cases/{feature_name}_usecase.py
      - Class: {PascalCase}UseCase  (e.g. register_worker -> RegisterWorkerUseCase)
    """
    registry = {}
    features_path = os.path.join(os.path.dirname(__file__), "app", "layer2_application", "features")
    base_module = "app.layer2_application.features"

    print(f"Scanning features in: {features_path}")

    for filepath in sorted(glob.glob(os.path.join(features_path, "*", "use_cases", "*_usecase.py"))):
        filename = os.path.basename(filepath)          # e.g. "register_worker_usecase.py"
        module_stem = filename[:-3]                     # e.g. "register_worker_usecase"
        feature_name = module_stem.removesuffix("_usecase")  # e.g. "register_worker"

        # PascalCase class name: register_worker -> RegisterWorkerUseCase
        class_name = "".join(part.capitalize() for part in feature_name.split("_")) + "UseCase"

        try:
            full_module_name = f"{base_module}.{feature_name}.use_cases.{module_stem}"
            module = importlib.import_module(full_module_name)

            if hasattr(module, class_name):
                registry[feature_name] = getattr(module, class_name)
                print(f"   [+] Found Feature: {feature_name}")
            else:
                print(f"   [!] {filename}: expected class {class_name} not found")
        except Exception as e:
            print(f"   [!] Error loading {feature_name}: {e}")

    return registry


def build_app_container():
    """
    1. Creates infrastructure instances (Layer 4)
    2. Discovers use case classes (Layer 2)
    3. Wires dependencies via smart injection
    """
    print(f"Bootstrapping {settings.APP_NAME}...")

    # 1. Shared infrastructure
    broker = _create_message_broker_client(settings)

    # 2. Service-specific adapters
    logger = StandardLogger()
    monitor = PrometheusMonitor()
    decision_switch = MockDecisionSwitchClient()

    # Task intake had no instrumentation: whether the executor is receiving
    # work was only visible as the absence of task logs, which is how the
    # control plane's AMQP outages stayed invisible. The broker is built before
    # the monitor, so it is wired in here rather than through the constructor.
    if broker is not None and hasattr(broker, "set_monitor"):
        broker.set_monitor(monitor)

    messaging_mode = (settings.MESSAGING_MODE or settings.INFRA_MODE).lower()

    if broker is not None and messaging_mode == "local":
        from app.layer4_frameworks.messaging.kafka_task_queue import KafkaTaskQueue
        from app.layer4_frameworks.messaging.kafka_result_publisher import KafkaResultPublisher

        task_queue = KafkaTaskQueue(broker, topic=settings.KAFKA_TASK_TOPIC)
        result_publisher = KafkaResultPublisher(broker, topic=settings.KAFKA_RESULT_TOPIC)
    elif broker is not None:
        from app.layer4_frameworks.messaging.event_mesh_task_queue import EventMeshTaskQueue
        from app.layer4_frameworks.messaging.event_mesh_result_publisher import EventMeshResultPublisher

        task_queue = EventMeshTaskQueue(broker)
        result_publisher = EventMeshResultPublisher(broker)
    else:
        task_queue = ConsoleTaskQueue()
        result_publisher = ConsoleResultPublisher()

    # 3. Worker registry store (B6: capped to bound the untrusted-write OOM vector)
    registry_store = MemoryWorkerRegistryStore(
        max_registered=getattr(settings, "MAX_REGISTERED_WORKERS", 0),
    )

    # 4. Worker dispatcher + static worker registration
    worker_dispatcher = WorkerDispatcher(registry_store=registry_store)

    # Phase 4 — the offload client. Absent unless a file service is configured;
    # see FILE_SERVICE_URL for why empty is the off switch.
    file_uploader = None
    if getattr(settings, "FILE_SERVICE_URL", ""):
        from app.layer4_frameworks.providers.http_file_uploader import HttpFileUploader
        file_uploader = HttpFileUploader(settings.FILE_SERVICE_URL)

    static_workers = [
        # ("wait-worker", settings.WAIT_WORKER_URL),
        # ("http-request-worker", settings.HTTP_REQUEST_WORKER_URL),
    ]
    for worker_name, worker_url in static_workers:
        worker_dispatcher.register_worker(
            worker_name,
            HttpWorkerExecutor(worker_url),
        )
        # Register in registry_store so static workers are visible to
        # list_workers, discover_workers, and get_worker_schema.
        registry_store.register(WorkerRegistration(
            id=worker_name,
            name=worker_name,
            capabilities=[WorkerCapability(domain=worker_name, action="execute")],
            endpoint_url=worker_url,
            worker_type=worker_name,
            version="built-in",
            sdk_version="built-in",
        ))

    # 5. Result reporter (HTTP or gRPC based on TRANSPORT_MODE).
    # In event_mesh mode there is no synchronous reporter — results flow back
    # to WCP via result_publisher (workflow.results topic).
    transport_mode = getattr(settings, "TRANSPORT_MODE", "http").lower()
    if transport_mode == "grpc":
        from app.layer4_frameworks.providers.grpc_result_reporter import GrpcResultReporter
        result_reporter = GrpcResultReporter(settings.WORKFLOW_CONTROL_PLANE_GRPC_TARGET)
    elif transport_mode == "event_mesh":
        result_reporter = None
    else:
        result_reporter = HttpResultReporter(settings.WORKFLOW_CONTROL_PLANE_URL)

    # 6. Pending task store + shared concurrency control (for async dispatch)
    # Max-in-flight is env-configurable (MAX_CONCURRENT_TASKS, default 50).
    # Tasks exceeding the cap get a CONCURRENCY_LIMIT error so the caller
    # can back off and retry rather than queueing indefinitely.
    pending_task_store = MemoryPendingTaskStore()
    concurrency_control = ConcurrencyControl(
        max_concurrent=settings.MAX_CONCURRENT_TASKS,
    )

    # 6b. Pull-delivery ready-task store (SA-2025). Only created when the feature
    # is enabled — otherwise it stays None and is filtered out of the DI pool
    # below, so ExecuteTaskUseCase gets ready_task_store=None and the pull branch
    # is dormant (push path byte-for-byte unchanged).
    if getattr(settings, "PULL_DELIVERY_ENABLED", False):
        from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore
        ready_task_store = MemoryReadyTaskStore(
            max_bytes_global=getattr(settings, "MAX_READY_BYTES_GLOBAL", 0),
            max_bytes_per_tenant=getattr(settings, "MAX_READY_BYTES_PER_TENANT", 0),
            max_bytes_per_partition=getattr(settings, "MAX_READY_BYTES_PER_PARTITION", 0),
        )
        print("   Pull delivery: ENABLED (ready-task store active)")
    else:
        ready_task_store = None

    # 7. Performance tracing
    trace_store = TaskTraceStore(max_traces=200)

    # Pool of all available dependencies (name must match __init__ param names)
    available_dependencies = {
        "logger": logger,
        "monitor": monitor,
        "app_config": settings,
        "task_queue": task_queue,
        "result_publisher": result_publisher,
        "decision_switch": decision_switch,
        "worker_dispatcher": worker_dispatcher,
        "result_reporter": result_reporter,
        "registry_store": registry_store,
        "pending_task_store": pending_task_store,
        "ready_task_store": ready_task_store,
        "concurrency_control": concurrency_control,
        "trace_store": trace_store,
        "message_broker_client": broker,
        # Phase 4 — only built when FILE_SERVICE_URL is configured. Left None
        # otherwise, and the filter below drops it, so an executor without a
        # file service keeps the pre-existing behaviour byte for byte.
        "file_uploader": file_uploader,
    }
    # Filter out None values so smart-DI skips optional deps that weren't
    # created (e.g. broker is None under INFRA_MODE=mock).
    available_dependencies = {k: v for k, v in available_dependencies.items() if v is not None}

    # 7. Discover features and inject dependencies (Smart Injection)
    feature_classes = scan_and_load_features()

    container = {}
    for name, UseCaseClass in feature_classes.items():
        sig = inspect.signature(UseCaseClass)
        injectable_kwargs = {
            param: dep
            for param, dep in available_dependencies.items()
            if param in sig.parameters
        }
        container[name] = UseCaseClass(**injectable_kwargs)

    # Store broker for shutdown access
    if broker is not None:
        container["_broker"] = broker

    # Expose raw infrastructure for observability endpoints
    container["_monitor"] = monitor
    container["_trace_store"] = trace_store
    container["_concurrency_control"] = concurrency_control
    container["_pending_task_store"] = pending_task_store
    container["_registry_store"] = registry_store

    return container
