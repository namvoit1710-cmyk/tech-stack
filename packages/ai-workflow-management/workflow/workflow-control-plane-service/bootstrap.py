"""Application container builder with smart dependency injection.

Scans feature use cases and injects only the dependencies each use case
declares in its constructor signature.
"""
import inspect
import logging
from typing import Optional

from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.config.instance_identity import resolve_instance_id
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.features import load_features, FEATURE_REGISTRY
from app.layer4_frameworks import create_infrastructure
from app.layer4_frameworks.auth.keycloak_admin import KeycloakAdminClient

from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
)
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.credentials import (
    RunCredentialProvider,
    collect_refs,
)
from app.layer4_frameworks.caching.bounded_ttl_cache import (
    BoundedTTLCache, RunScopedTTLCache, env_int,
)
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.definition.node_definition_resolver import NodeDefinitionResolver
from app.layer1_domain.value_objects.resolution_scope import ResolutionScopeBuilder
from app.layer2_application.providers import (
    BuiltinNodeProvider,
    BusinessNodeProvider,
    WorkerNodeProvider,
)
from app.layer4_frameworks.caching import NodeCatalogCache
from app.layer4_frameworks.messaging.consumers import CatalogChangedConsumer
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer1_domain.value_objects.node_input_etag import NodeInputEtag
from app.layer2_application.data.row_path_resolver import RowPathResolver
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
from app.layer2_application.node_handlers.loop_exit_handler import LoopExitNodeHandler
from app.layer2_application.node_handlers.loop_continue_handler import LoopContinueNodeHandler
from app.layer2_application.node_handlers.parallel_handler import ParallelNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.approval_flow_handler import ApprovalFlowNodeHandler
from app.layer2_application.read.approval_process_transformer import ApprovalProcessTransformer
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.node_handlers.human_action_handler import HumanActionNodeHandler
from app.layer2_application.node_handlers.data_edit_handler import DataEditNodeHandler
from app.layer2_application.node_handlers.template_data_edit_handler import TemplateDataEditNodeHandler
from app.layer2_application.node_handlers.file_upload_handler import FileUploadNodeHandler
from app.layer2_application.node_handlers.output_handler import OutputNodeHandler
from app.layer2_application.node_handlers.approval_flow_handler import ApprovalFlowNodeHandler
from app.layer2_application.node_handlers.set_variable_handler import SetVariableHandler
from app.layer2_application.node_handlers.prompt_handler import PromptNodeHandler
from app.layer2_application.node_handlers.wait_handler import WaitNodeHandler

from app.layer2_application.interfaces.event_log_repository_interface import IEventLogRepository
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import MemoryEventLogRepository

_logger = logging.getLogger(__name__)


def _build_event_log_repo(settings, infra=None) -> IEventLogRepository:
    """Select the event log backend based on ``EVENT_LOG_BACKEND``.

    Phase 4b.7: the default remains ``"memory"`` so existing
    deployments see no behavior change. Enabling ``"sqlite"`` via env
    var or ``.env`` gives durable event log storage and is the
    prerequisite for flipping the Phase 4c.2 projector read model on
    in production.

    Phase 4b.8: ``"hana"`` selects the SAP HANA backend, which reuses
    the ``HanaConnectionManager`` already created by
    ``create_infrastructure`` and stored at ``infra.db``. When
    ``infra.db`` is ``None`` (INFRA_MODE != "sap" or HANA initialisation
    failed), the HANA branch falls back to memory with a warning —
    we must never fail engine startup because of a misconfigured
    shadow log.

    Unknown values also fall back to ``"memory"`` with a warning.
    """
    backend = (getattr(settings, "EVENT_LOG_BACKEND", "memory") or "memory").lower()
    db_path = getattr(settings, "EVENT_LOG_DB_PATH", ":memory:") or ":memory:"

    if backend == "sqlite":
        from app.layer4_frameworks.persistence.sqlite.sqlite_event_log_repo import (
            SqliteEventLogRepository,
        )
        _logger.info("Event log backend: sqlite (db_path=%s)", db_path)
        return SqliteEventLogRepository(db_path)

    if backend == "hana":
        hana_db = getattr(infra, "db", None) if infra is not None else None
        if hana_db is None:
            _logger.warning(
                "EVENT_LOG_BACKEND=hana but no HANA connection is available "
                "(infra.db is None — check INFRA_MODE and HANA_* settings). "
                "Falling back to in-memory event log.",
            )
            return MemoryEventLogRepository()
        from app.layer4_frameworks.persistence.hana.hana_event_log_repo import (
            HanaEventLogRepository,
        )
        _logger.info(
            "Event log backend: hana (shared HanaConnectionManager from infra.db)",
        )
        return HanaEventLogRepository(hana_db)

    if backend != "memory":
        _logger.warning(
            "Unknown EVENT_LOG_BACKEND=%r; falling back to 'memory'.", backend,
        )

    _logger.info("Event log backend: memory (ephemeral)")
    return MemoryEventLogRepository()


def _build_timer_repo(settings, infra=None):
    """Select the durable-timer store backend (SA-1371).

    Mirrors ``_build_event_log_repo``: ``TIMER_BACKEND`` chooses ``memory``
    (default, ephemeral) / ``sqlite`` (durable local file, uses
    ``TIMER_DB_PATH``) / ``hana`` (shares the ``HanaConnectionManager`` at
    ``infra.db``). The ``hana`` branch falls back to memory with a warning when
    no HANA connection is available so a misconfigured store never fails engine
    startup. Unknown values also fall back to ``memory``.
    """
    from app.layer4_frameworks.persistence.memory.memory_timer_repo import (
        MemoryTimerRepository,
    )

    backend = (getattr(settings, "TIMER_BACKEND", "memory") or "memory").lower()
    db_path = getattr(settings, "TIMER_DB_PATH", ":memory:") or ":memory:"

    if backend == "sqlite":
        from app.layer4_frameworks.persistence.sqlite.sqlite_timer_repo import (
            SqliteTimerRepository,
        )
        _logger.info("Timer backend: sqlite (db_path=%s)", db_path)
        return SqliteTimerRepository(db_path)

    if backend == "hana":
        hana_db = getattr(infra, "db", None) if infra is not None else None
        if hana_db is None:
            _logger.warning(
                "TIMER_BACKEND=hana but no HANA connection is available "
                "(infra.db is None — check INFRA_MODE and HANA_* settings). "
                "Falling back to in-memory timer store.",
            )
            return MemoryTimerRepository()
        from app.layer4_frameworks.persistence.hana.hana_timer_repo import (
            HanaTimerRepository,
        )
        _logger.info(
            "Timer backend: hana (shared HanaConnectionManager from infra.db)",
        )
        return HanaTimerRepository(hana_db)

    if backend != "memory":
        _logger.warning(
            "Unknown TIMER_BACKEND=%r; falling back to 'memory'.", backend,
        )

    _logger.info("Timer backend: memory (ephemeral)")
    return MemoryTimerRepository()


def _build_worker_config_repo(settings, infra=None):
    """SA-2437 — per-workflow worker-instance config store. HANA when a
    HanaConnectionManager is available (INFRA_MODE local/sap), else in-memory
    (mock/local without HANA). Never fails startup."""
    from app.layer4_frameworks.persistence.memory.memory_worker_config_repo import (
        MemoryWorkerConfigRepository,
    )
    hana_db = getattr(infra, "db", None) if infra is not None else None
    if hana_db is not None:
        from app.layer4_frameworks.persistence.hana.hana_worker_config_repo import (
            HanaWorkerConfigRepository,
        )
        _logger.info("Worker-config backend: hana (shared HanaConnectionManager)")
        return HanaWorkerConfigRepository(hana_db)
    _logger.info("Worker-config backend: memory (ephemeral)")
    return MemoryWorkerConfigRepository()


def _http_probe_callable():
    """Return the production IHttpProbe (httpx-based)."""
    from app.layer4_frameworks.security.httpx_probe import httpx_probe
    return httpx_probe


def _build_vault_credential_client(settings):
    """Build the run-time vault credential client (credentials bridge, Q3 = REST).

    ``CREDENTIALS_VAULT_URL`` is the activation flag for the StartRun producer step
    that resolves node ``credential_ref``s from the vault. When it is unset this
    returns ``None`` and the producer stays dormant — ``StartRunUseCase`` falls back
    to author-supplied ``runtime_credentials`` and nothing changes. Flip it on once
    the ``credentials-management`` vault ships its authenticated
    ``GET /api/v1/internal/credentials/:id/resolve`` endpoint (Part A).
    """
    vault_url = (getattr(settings, "CREDENTIALS_VAULT_URL", "") or "").strip()
    if not vault_url:
        _logger.info(
            "CREDENTIALS_VAULT_URL not set — vault credential_ref resolution disabled "
            "(producer dormant; inline/author credentials still work).",
        )
        return None
    from app.layer4_frameworks.clients.http_vault_credential_client import (
        VaultCredentialClient,
    )
    service_token = (getattr(settings, "CREDENTIALS_VAULT_SERVICE_TOKEN", "") or "").strip() or None
    _logger.info("Vault credential client enabled (url=%s)", vault_url)
    return VaultCredentialClient(base_url=vault_url, service_token=service_token)


def _build_credential_cipher(settings):
    """Build the AES-GCM credential cipher used by the gateway service registry.

    Reads `GATEWAY_REGISTRY_KEK_V<n>` env vars (and optional `GATEWAY_REGISTRY_KEK_CURRENT_VERSION`).
    If none are set, falls back to a deterministic dev key in mock mode so local
    workflows don't need to set env vars. Returns ``None`` when not in mock mode
    and no env vars are present, which means the gateway-registry use cases get
    skipped by the auto-discovery loop (the feature is unusable without a key).
    """
    from app.layer4_frameworks.security.aes_gcm_credential_cipher import (
        AesGcmCredentialCipher,
    )

    try:
        return AesGcmCredentialCipher.from_env()
    except ValueError:
        if getattr(settings, "INFRA_MODE", "mock").lower() == "mock":
            _logger.warning(
                "GATEWAY_REGISTRY_KEK_V<n> not set — using dev fallback key. "
                "DO NOT use in production."
            )
            return AesGcmCredentialCipher(
                current_version=1,
                keys={1: b"\x00" * 32},
            )
        _logger.warning(
            "GATEWAY_REGISTRY_KEK_V<n> not set; gateway service registry use cases "
            "will be unavailable."
        )
        return None


def _build_snapshot_repo(settings, infra=None):
    """Select the snapshot-store backend (Phase 4.5).

    Under ``SNAPSHOT_MODE=off`` (default) returns ``None`` — the view
    store skips all snapshot logic and the system behaves exactly as
    pre-Phase-4. Under ``shadow`` / ``authoritative`` we wire either
    the HANA backend (``INFRA_MODE=sap`` with ``infra.db`` available)
    or a ``MemorySnapshotRepository`` fallback.

    Snapshots are paired to the event log: durability only matters if
    the log itself is durable. The memory fallback is acceptable for
    dev / sqlite-backed deployments where the cold-start concern is
    moot.
    """
    mode = (getattr(settings, "SNAPSHOT_MODE", "off") or "off").lower()
    if mode == "off":
        return None

    hana_db = getattr(infra, "db", None) if infra is not None else None
    if hana_db is not None:
        from app.layer4_frameworks.persistence.hana.hana_snapshot_repository import (
            HanaSnapshotRepository,
        )
        _logger.info("Snapshot store: hana (shared HanaConnectionManager)")
        return HanaSnapshotRepository(hana_db)

    from app.layer4_frameworks.persistence.memory.memory_snapshot_repository import (
        MemorySnapshotRepository,
    )
    _logger.info("Snapshot store: memory (ephemeral — use HANA in production)")
    return MemorySnapshotRepository()


def _build_worker_mirror_repo(settings, infra=None):
    """Build the worker-registry observability mirror repo, or ``None`` in mock
    mode — SA-2050 (R3).

    Returns a ``HanaWorkerRegistryMirrorRepository`` bound to the shared
    ``HanaConnectionManager`` (``infra.db``) when HANA is configured
    (INFRA_MODE local/sap). Returns ``None`` when ``infra.db`` is ``None`` (mock
    / no HANA) so the reconcile loop no-ops (spec §7). Cross-wired into the
    ``worker_mirror_reconcile`` use case in ``build_app_container``.
    """
    hana_db = getattr(infra, "db", None) if infra is not None else None
    if hana_db is None:
        return None
    from app.layer4_frameworks.persistence.hana.hana_worker_registry_mirror_repo import (
        HanaWorkerRegistryMirrorRepository,
    )
    return HanaWorkerRegistryMirrorRepository(hana_db)


def _install_cache_budget_collector(monitor) -> None:
    """Attach the SA-2256 cache-budget collector to the /metrics registry.

    Best-effort: a monitor without a Prometheus registry (the stub used in
    tests) simply gets nothing. Observability must not be able to stop the
    service from booting.
    """
    try:
        from app.layer4_frameworks.monitoring.cache_budget_collector import (
            install_cache_budget_collector,
        )
        if install_cache_budget_collector(monitor) is not None:
            _logger.info("Cache budget metrics installed on /metrics (SA-2256)")
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("Cache budget collector not installed: %s", exc)


def _install_resource_collectors(monitor) -> None:
    """Attach RSS/CPU/fd, container memory and disk metrics to /metrics.

    The monitor owns a PRIVATE registry, which is why prometheus_client's
    default collectors were never present: nothing registered them. Same
    best-effort contract as the cache budget collector -- observability must
    not be able to stop the service from booting.
    """
    try:
        from app.layer4_frameworks.monitoring.resource_collector import (
            install_resource_collector,
            install_standard_process_collectors,
        )
        if install_standard_process_collectors(monitor):
            _logger.info("Process metrics installed on /metrics (RSS, CPU, fds)")
        if install_resource_collector(monitor) is not None:
            _logger.info("Container memory and disk metrics installed on /metrics")
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("Resource collectors not installed: %s", exc)


def _build_run_context_view_store(
    settings, repos, event_log_repo, *, monitor=None, drift_recorder=None,
    snapshot_repo=None, snapshot_monitor=None, schema_dictionary=None,
):
    """Build the read-side view store.

    Phase 3b.3: simplified to always return a
    ``CachedProjectorRunContextViewStore`` — the projector has been
    authoritative in prod since Phase 1b. Tri-state selection
    (``live`` / ``shadow`` / ``projector``) and the ``live`` + parity
    comparator stores are deleted.

    Phase 4: ``snapshot_repo`` wires the optional snapshot store. When
    ``None`` (default / ``SNAPSHOT_MODE=off``), snapshot logic is a
    no-op.

    ``monitor`` / ``drift_recorder`` kwargs are accepted for signature
    compat with any older callers but are no longer consumed.
    """
    del monitor, drift_recorder  # retained for signature compat
    _ = getattr(settings, "PROJECTOR_READ_MODE", None)  # no-op; flag removed

    from app.layer4_frameworks.run_context.cached_projector_view_store import (
        CachedProjectorRunContextViewStore,
    )
    from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
        MemoryEventLogRepository,
    )

    if isinstance(event_log_repo, MemoryEventLogRepository):
        _logger.warning(
            "EVENT_LOG_BACKEND is 'memory' — projector state is "
            "ephemeral. Set EVENT_LOG_BACKEND=sqlite or =hana in "
            "production.",
        )

    snapshot_mode = (getattr(settings, "SNAPSHOT_MODE", "off") or "off").lower()
    _logger.info(
        "Read model: projector-backed "
        "(event log + workflow_repo/run_repo fallback, snapshot_mode=%s)",
        snapshot_mode,
    )
    return CachedProjectorRunContextViewStore(
        event_log_repo,
        workflow_repo=repos.workflow,
        run_repo=repos.run,
        snapshot_repo=snapshot_repo,
        snapshot_mode=snapshot_mode,
        snapshot_every_n_events=int(getattr(
            settings, "SNAPSHOT_EVERY_N_EVENTS", 100)),
        snapshot_every_t_seconds=int(getattr(
            settings, "SNAPSHOT_EVERY_T_SECONDS", 300)),
        snapshot_keep_k_back=int(getattr(
            settings, "SNAPSHOT_KEEP_K_BACK", 2)),
        monitor=snapshot_monitor,
        schema_dictionary=schema_dictionary,
    )


def _build_handler_registry(
    variable_resolver: VariableResolver,
    condition_evaluator: ConditionEvaluator,
    worker_registry_client=None,
    data_store_service=None,
    schema_transform_client=None,
    file_service_client=None,
    governance_client=None,
    credential_cipher=None,
    integration_hub_client=None,
    credential_provider=None,
) -> NodeHandlerRegistry:
    """Wire all node handlers into the registry."""
    registry = NodeHandlerRegistry()
    # TASK is the egress boundary: it fetches the node's own credential_ref at
    # dispatch through ``credential_provider`` (SA-2287 / B3). ``credential_cipher``
    # stays for the run's sealed author-supplied map.
    registry.register(NodeType.TASK, TaskNodeHandler(variable_resolver, worker_registry_client=worker_registry_client, credential_cipher=credential_cipher, credential_provider=credential_provider))
    registry.register(NodeType.CONDITION, ConditionNodeHandler(condition_evaluator))
    registry.register(NodeType.SWITCH, SwitchNodeHandler(variable_resolver))
    registry.register(NodeType.LOOP, LoopNodeHandler(variable_resolver, data_store_service=data_store_service))
    registry.register(NodeType.LOOP_EXIT, LoopExitNodeHandler())
    registry.register(NodeType.LOOP_CONTINUE, LoopContinueNodeHandler())
    registry.register(NodeType.PARALLEL, ParallelNodeHandler(variable_resolver))
    registry.register(NodeType.MERGE, MergeNodeHandler())
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.WORKFLOW, WorkflowNodeHandler(variable_resolver))
    registry.register(NodeType.COMPUTE, ComputeNodeHandler(variable_resolver))
    registry.register(NodeType.APPROVAL_FLOW, ApprovalFlowNodeHandler(variable_resolver, governance_client=governance_client))
    registry.register(NodeType.INPUT, InputNodeHandler(
        variable_resolver,
        data_store_service=data_store_service,
        schema_transform_client=schema_transform_client,
        file_service_client=file_service_client,
    ))
    registry.register(NodeType.HUMAN_ACTION, HumanActionNodeHandler(
        variable_resolver,
        file_service_client=file_service_client,
        data_store_service=data_store_service,
    ))
    registry.register(NodeType.DATA_EDIT, DataEditNodeHandler(
        variable_resolver,
        file_service_client=file_service_client,
        data_store_service=data_store_service,
    ))
    registry.register(NodeType.TEMPLATE_DATA_EDIT, TemplateDataEditNodeHandler(
        variable_resolver,
        file_service_client=file_service_client,
        data_store_service=data_store_service,
        governance_client=governance_client,
        integration_hub_client=integration_hub_client,
    ))
    registry.register(NodeType.FILE_UPLOAD, FileUploadNodeHandler(
        variable_resolver,
        file_service_client=file_service_client,
        data_store_service=data_store_service,
    ))
    registry.register(NodeType.OUTPUT, OutputNodeHandler(
        variable_resolver,
        data_store_service=data_store_service,
    ))
    registry.register(NodeType.APPROVAL_FLOW, ApprovalFlowNodeHandler(
        variable_resolver,
        governance_client=governance_client,
    ))
    registry.register(NodeType.SET_VARIABLE, SetVariableHandler(variable_resolver))
    registry.register(NodeType.PROMPT, PromptNodeHandler(variable_resolver))
    # WAIT (SA-1371 S4) — parks the run like INPUT and schedules a durable
    # timer. ``create_timer_use_case`` is post-wired in ``build_app_container``
    # after the feature loop builds the ``create_timer`` use case (it depends
    # on the fully-teed event_publisher + timer_repo, both wired later).
    registry.register(NodeType.WAIT, WaitNodeHandler(variable_resolver))
    return registry


def build_app_container(sio_server=None) -> dict:
    _logger.info("Bootstrapping %s...", settings.APP_NAME)

    # 1. Infrastructure (all adapters created via factory)
    infra = create_infrastructure(settings)
    log = infra.logger
    monitor = infra.monitor
    repos = infra.repos

    # 2. Build publisher decorator chain (outermost → innermost):
    #    EventLogAppending → RealtimeBroadcasting → EnrichingPublisher → Kafka/EventMesh/Console
    #
    # ``EventLogAppendingPublisher`` is the outermost decorator so every
    # event hits the append-only event log BEFORE any broadcast side
    # effects — write-ahead log semantics. Shadow log in Phase 4b.1; the
    # Projector (Phase 4b.3) will rebuild state from it, and Phase 5
    # makes it authoritative.
    from app.layer4_frameworks.messaging.event_enricher import CentralizedEventEnricher
    from app.layer4_frameworks.messaging.enriching_event_publisher import EnrichingEventPublisher
    from app.layer4_frameworks.messaging.event_log_appending_publisher import (
        EventLogAppendingPublisher,
    )
    from app.layer4_frameworks.messaging.metadata_stamping_publisher import (
        MetadataStampingPublisher,
    )
    from app.layer4_frameworks.realtime.broadcasting_publisher import RealtimeBroadcastingPublisher

    # 2a. Event log repository (Phase 4b.1 foundation + Phase 4b.7 backend
    # selection + Phase 4b.8 HANA option). Default is
    # ``MemoryEventLogRepository`` via the ``EVENT_LOG_BACKEND=memory``
    # setting. Set ``EVENT_LOG_BACKEND=sqlite`` +
    # ``EVENT_LOG_DB_PATH=/path/to/events.db`` for durable file-backed
    # storage, or ``EVENT_LOG_BACKEND=hana`` with ``INFRA_MODE=sap`` to
    # reuse the HanaConnectionManager from ``infra.db`` for production
    # HANA persistence.
    event_log_repo = _build_event_log_repo(settings, infra=infra)

    # Phase 4 — projector snapshot store. Returns ``None`` under
    # ``SNAPSHOT_MODE=off`` (default); in that case the view store
    # behaves identically to pre-Phase-4.
    snapshot_repo = _build_snapshot_repo(settings, infra=infra)

    enricher = CentralizedEventEnricher(
        workflow_repo=repos.workflow,
        run_repo=repos.run,
    )
    enriched_publisher = EnrichingEventPublisher(
        inner=infra.messaging.event_publisher,
        broker_client=infra.broker,
        enricher=enricher,
    )
    realtime_publisher = RealtimeBroadcastingPublisher(
        inner_publisher=enriched_publisher,
        broker=infra.broker,
    )
    # Outermost decorator: stamp caller-supplied run metadata onto every event
    # before the event log / realtime / centralized publishers serialize it, so
    # the metadata is echoed back on every outbound external message.
    event_publisher = MetadataStampingPublisher(
        inner=EventLogAppendingPublisher(
            inner=realtime_publisher,
            event_log_repo=event_log_repo,
            logger=log,
        ),
        run_repo=repos.run,
    )

    # Phase 2b — DispatcherConsumer in shadow mode. The consumer is
    # wired only when ``DISPATCHER_MODE=shadow``. Inline routing in
    # ``OrchestrationEngine._route_to_successors`` continues to own
    # emission; the consumer records what it *would* emit so the
    # parity comparator can assert drift=0 before Phase 3a flips the
    # authoritative bit. The consumer needs the view store + workflow
    # repo, so it is instantiated later, after those are built; the
    # variables are reserved here so ``event_publisher`` can be
    # re-wrapped once the consumer exists.
    dispatcher_consumer = None
    dispatcher_parity_comparator = None

    # 2b. The realtime consumer subscribes to the broker topic so per-run
    # ordering is preserved end-to-end (publisher → broker partition →
    # consumer → sink). When broker is None (INFRA_MODE=mock), the
    # direct-notify path is enabled by wiring the consumer into the publisher
    # so events bypass the broker.
    #
    # The Socket.IO sink was removed: every environment now delivers realtime
    # to the frontend through the Push Gateway, so the Socket.IO consumer was
    # a second subscriber on ``realtime.events`` feeding rooms nobody joins.
    # Its per-run resequencing is not lost — ``PushGatewayRealtimeConsumer``
    # carries its own reorder buffer with the same semantics and env knobs.
    push_gateway_url = getattr(settings, "PUSH_GATEWAY_URL", "")
    if push_gateway_url:
        from app.layer4_frameworks.realtime.push_gateway import create_client, get_registry
        from app.layer4_frameworks.realtime.push_gateway_consumer import PushGatewayRealtimeConsumer

        provider = getattr(settings, "PUSH_GATEWAY_PROVIDER", "http")
        available = get_registry().list_providers()

        if provider not in available:
            _logger.warning(
                "PUSH_GATEWAY_PROVIDER=%s not available (registered: %s), falling back to 'http'",
                provider, available,
            )
            provider = "http"

        if provider == "http":
            pg_client = create_client("http", base_url=push_gateway_url)
        elif provider == "grpc":
            pg_client = create_client("grpc", target=push_gateway_url)
        else:
            pg_client = create_client(provider, base_url=push_gateway_url)

        push_gateway_consumer = PushGatewayRealtimeConsumer(
            client=pg_client,
            broker=infra.broker,
        )
        realtime_publisher.add_realtime_consumer(push_gateway_consumer)
        _logger.info("Push Gateway consumer enabled (provider=%s, url=%s)", provider, push_gateway_url)
    else:
        push_gateway_consumer = None

    # 3. Domain services
    stage_recorder = StageRecorder(repos.stage_history)
    task_factory = TaskFactory()
    csv_converter = CsvConverter()
    node_definition_resolver = NodeDefinitionResolver()

    # File service client: fall back to in-memory mock when not configured
    file_service_client = infra.messaging.file_service_client
    if file_service_client is None:
        from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
        file_service_client = MockFileServiceClient()

    # Data store service for file-backed node I/O
    data_store_service = DataStoreService(
        file_service_client,
        csv_converter,
        chunked_output_enabled=settings.WORKER_CHUNKED_OUTPUT_ENABLED,
        chunking_threshold_bytes=settings.CHUNKING_THRESHOLD_BYTES,
        recursive_split=settings.RECURSIVE_ARTIFACT_SPLIT,
        # SA-1905 — the nested Arrow sidecar. All three default to the values
        # that change nothing.
        arrow_read_mode=settings.ARROW_READ_MODE,
        arrow_min_bytes=settings.ARROW_SIDECAR_MIN_BYTES,
        native_split=settings.NATIVE_RECURSIVE_SPLIT,
        orphan_gc_enabled=settings.ORPHAN_GC_ENABLED,
        arrow_write_enabled=settings.ARROW_SIDECAR_WRITE_ENABLED,
        arrow_strict_parity=settings.ARROW_STRICT_PARITY,
        arrow_codec=settings.ARROW_SIDECAR_COMPRESSION,
        materialize_max_bytes=settings.RESOLVE_MATERIALIZE_MAX_BYTES,
        # SA-2256 — full_materialize_total; gate G6 depends on it permanently.
        monitor=monitor,
    )

    # SA-2256 — publish the 256MB memory budget on the existing /metrics
    # scrape. Pull-based: each cache is read at scrape time, so a bucket that
    # grows between scrapes is never invisible. No new egress.
    _install_cache_budget_collector(monitor)

    # RSS, container memory limit/usage, and disk. The cache budget above is an
    # ESTIMATE built from per-entry size constants; these are the measured
    # numbers those estimates are supposed to approximate, and without them a
    # budget can be tuned for months against nothing.
    _install_resource_collectors(monitor)

    # Node-data write-path services (Unit D). Phase 3c: PATCH serialises
    # at the orchestration worker (queue-routed) — the prior in-process
    # ``RunLockRegistry`` was deleted.
    node_input_etag_calculator = NodeInputEtag()
    row_path_resolver = RowPathResolver(file_service_client)

    # Data factory client: fall back to mock when not configured
    data_factory_url = getattr(settings, "DATA_FACTORY_URL", "")
    if data_factory_url:
        from app.layer4_frameworks.clients.http_data_factory_client import HttpDataFactoryClient
        data_factory_client = HttpDataFactoryClient(data_factory_url)
    else:
        from app.layer4_frameworks.clients.mock_data_factory_client import MockDataFactoryClient
        data_factory_client = MockDataFactoryClient(file_service_client)

    # Node-data factory service: higher-level port wrapping ``data_factory_client``.
    # Used by the ``edit_node_data_via_factory`` PATCH variant. Future backends
    # (different vendor, local compute, streaming) plug in here without
    # touching the use case.
    from app.layer4_frameworks.services.row_transform_factory_service import (
        RowTransformFactoryService,
    )
    node_data_factory_service = RowTransformFactoryService(data_factory_client)

    # Schema transform client: always use real HTTP service
    schema_transform_url = getattr(settings, "SCHEMA_TRANSFORM_URL", "") or "https://smdg-ai-dev-schema-transform-service-qas.cfapps.br10.hana.ondemand.com"
    from app.layer4_frameworks.clients.http_schema_transform_client import HttpSchemaTransformClient
    schema_transform_client = HttpSchemaTransformClient(schema_transform_url)

    # Governance API client: used by APPROVAL_FLOW node handler
    default_governance_api = "https://smdg-ai-dev-governance-dev.cfapps.br10.hana.ondemand.com"
    governance_api_url = getattr(settings, "GOVERNANCE_API_URL", "") or default_governance_api
    from app.layer4_frameworks.clients.http_governance_client import HttpGovernanceClient
    governance_client = HttpGovernanceClient(governance_api_url) if governance_api_url else None

    # Integration Hub client: used by TEMPLATE_DATA_EDIT for BDM search-help.
    default_integration_hub_api = "https://integration-hub-dev.cfapps.br10.hana.ondemand.com"
    integration_hub_api_url = getattr(settings, "INTEGRATION_HUB_API_URL", "") or default_integration_hub_api
    from app.layer4_frameworks.clients.http_integration_hub_client import HttpIntegrationHubClient
    integration_hub_client = HttpIntegrationHubClient(integration_hub_api_url) if integration_hub_api_url else None

    # Node-output descriptor store — AIW_RUN_NODE_OUTPUT + AIW_SCHEMA_DEF
    # (migration 055). Built HERE, at the top of the graph, because everything
    # downstream shares this ONE instance: the resolver's schema dictionary
    # (just below), the orchestration engine's mirror — the single writer, which
    # is what finally gave builtins their rows — and the read use cases.
    #
    # HANA only. Without a connection this stays None: the mirror is a no-op,
    # the dictionary answers nothing and every reader keeps its inline copy.
    _node_output_db = getattr(infra, "db", None) if infra is not None else None
    node_output_repo = None
    if _node_output_db is not None:
        from app.layer4_frameworks.persistence.hana.hana_node_output_repo import (
            HanaNodeOutputRepository,
        )
        node_output_repo = HanaNodeOutputRepository(_node_output_db)

    # AIW_SCHEMA_DEF lookup for the resolver. Inert at SCHEMA_REF_MODE=off; in
    # `shadow` it reports what a hash-only ref would lose, on real traffic,
    # before anything is removed.
    _schema_dictionary = None
    if node_output_repo is not None:
        from app.layer2_application.data.schema_dictionary import SchemaDictionary
        _schema_dictionary = SchemaDictionary(node_output_repo.get_schemas)

    # Insert file-backed resolver as the first strategy
    file_backed_strategy = FileBackedResolverStrategy(
        data_store_service, file_client=file_service_client,
        schema_dictionary=_schema_dictionary,
    )
    resolver = VariableResolver(
        strategies=[
            file_backed_strategy,
            MetadataResolverStrategy(),
            # The monitor is what makes ``variable_resolve_envelope_bare_read``
            # exist: the traversal strategy is where an expression can read a
            # file_ref's reference metadata instead of its data, and the count
            # is how the decision to refuse that gets made on numbers rather
            # than on reasoning. Unwired, the shadow reads a reassuring zero.
            TraversalResolverStrategy(monitor=monitor),
        ],
        monitor=monitor,
    )
    # SA-2273 — a CONDITION over a file-backed collection used to compare
    # against the reference ENVELOPE rather than the data. The probe answers
    # membership at the file service ($filter + $count, zero rows returned);
    # without it the evaluator falls back to the previous behaviour.
    from app.layer2_application.resolve.collection_membership import (
        CollectionMembershipProbe,
    )

    condition_evaluator = ConditionEvaluator(
        resolver,
        membership_probe=CollectionMembershipProbe(
            file_service_client, monitor=monitor,
        ),
    )
    # Build cipher early so it can be threaded into handler_registry and
    # resolution_scope_builder (both need it for credential decrypt).
    credential_cipher = _build_credential_cipher(settings)
    vault_credential_client = _build_vault_credential_client(settings)
    # One provider for every egress point: TASK dispatch and the $credentials
    # expression frame. ``run_repo`` is what lets a sub-workflow's node resolve
    # as the ROOT run's identity instead of silently going user-less.
    #
    # SA-2290 (B6) — the cache is built HERE, not inside the provider: it is a
    # layer4 class and the provider is layer2, and this file is the composition
    # root that is allowed to know both. Naming it enrols it in the SA-2254
    # memory budget, so it shows up in the wcp_cache_* metrics like every other
    # bounded cache rather than growing unobserved.
    #
    # The TTL is the revocation window: a credential withdrawn in the vault
    # keeps working for at most this long in a run that already resolved it.
    # Five minutes trades a little staleness for not re-hitting the vault on
    # every node of a long run. Set CREDENTIAL_CACHE_TTL_S=0 to disable.
    _credential_cache_ttl = env_int("CREDENTIAL_CACHE_TTL_S", 300, minimum=0)
    credential_provider = RunCredentialProvider(
        vault_client=vault_credential_client,
        cipher=credential_cipher,
        run_repo=repos.run,
        # SA-2368 — run-scoped, so the decrypted secret dies with the run
        # instead of waiting for 512 later runs to push it out by LRU. The key
        # is ``(root_run_id, tenant_id, ref)``; element 0 is deliberately the
        # ROOT id, which is what makes eviction correct for sub-workflows: a
        # child reaching a terminal state must NOT drop credentials the still
        # running root is using, and when the root terminates its entries go
        # even though a child created some of them.
        cache=RunScopedTTLCache(
            maxsize=env_int("CREDENTIAL_CACHE_MAX", 512),
            ttl_seconds=_credential_cache_ttl,
            name="run_credentials",
            run_id_of_key=lambda key: key[0],
        ) if _credential_cache_ttl else None,
        cache_ttl_seconds=_credential_cache_ttl,
    )
    handler_registry = _build_handler_registry(
        resolver, condition_evaluator,
        worker_registry_client=infra.messaging.worker_registry_client,
        data_store_service=data_store_service,
        schema_transform_client=schema_transform_client,
        file_service_client=file_service_client,
        governance_client=governance_client,
        credential_cipher=credential_cipher,
        integration_hub_client=integration_hub_client,
        credential_provider=credential_provider,
    )

    # Phase 2a — build the read-side view store before any orchestration
    # service that consumes it, so engine / successor processor / loop
    # driver reads can go through ``IRunContextViewStore`` rather than
    # direct ``run.context.*`` aggregate access. Phase 3b.3 dropped the
    # tri-state mode dispatch; the projector-backed store is the only
    # option.
    run_context_view_store = _build_run_context_view_store(
        settings, repos, event_log_repo,
        snapshot_repo=snapshot_repo,
        snapshot_monitor=monitor,
        # Under SCHEMA_REF_MODE=on this is what puts a thin ref's schema back
        # before ANY reader sees `variables` — the resolver is too late, six
        # other modules read a ref's columns without passing through it.
        schema_dictionary=_schema_dictionary,
    )

    # Phase 2b — instantiate DispatcherConsumer + parity comparator
    # when ``DISPATCHER_MODE=shadow``. Wraps ``event_publisher`` with
    # a tee that feeds ``NodeOutputProduced`` events to the consumer.
    # Inline engine routing is unaffected.
    # Phase 3a PR 2 — dispatcher is the only authoritative emitter of
    # EdgeTraversed / NodeInput / WireSkipped / WireDeadLetter. ``inline``
    # and unknown values upgrade to ``dispatcher`` with a warning.
    dispatcher_mode = getattr(settings, "DISPATCHER_MODE", "dispatcher")
    if dispatcher_mode not in ("shadow", "dispatcher"):
        if dispatcher_mode == "inline":
            _logger.warning(
                "DISPATCHER_MODE=inline is no longer supported "
                "(engine inline edge emission deleted in Phase 3a PR 2). "
                "Upgrading to 'dispatcher'.",
            )
        else:
            _logger.warning(
                "DISPATCHER_MODE=%r not recognised, falling back to "
                "'dispatcher'. Valid values: dispatcher, shadow.",
                dispatcher_mode,
            )
        dispatcher_mode = "dispatcher"

    from app.layer4_frameworks.dispatcher import (
        DispatcherConsumer,
        DispatcherParityComparator,
        DispatcherParityRecorder,
        DispatcherTeeingPublisher,
    )
    # Comparator stays wired in ``shadow`` mode as a one-release
    # diagnostic aid; in ``dispatcher`` mode it is not constructed.
    if dispatcher_mode == "shadow":
        dispatcher_parity_recorder = DispatcherParityRecorder(monitor=monitor)
        dispatcher_parity_comparator = DispatcherParityComparator(
            recorder=dispatcher_parity_recorder,
        )
    else:
        dispatcher_parity_recorder = None
        dispatcher_parity_comparator = None
    dispatcher_consumer = DispatcherConsumer(
        view_store=run_context_view_store,
        workflow_repo=repos.workflow,
        recorder=dispatcher_parity_recorder,
        mode=dispatcher_mode,
    )
    event_publisher = DispatcherTeeingPublisher(
        inner=event_publisher,
        dispatcher=dispatcher_consumer,
    )
    # The dispatcher's own publisher is bound below, AFTER the full tee
    # chain is assembled — the binding is mode-dependent (see the
    # ``dispatcher_consumer.set_publisher`` call past the consumer
    # wiring). Keep a reference to this partial chain for the modes that
    # use it.
    dispatcher_tee_publisher = event_publisher

    # Phase 4 Sub 4.1 — TaskCreatorConsumer.
    #
    # When ``EXECUTOR_MODE != in_process`` the engine no longer creates
    # downstream Tasks inline (``_exec_uniform`` post-result code is
    # bypassed). Without this consumer the standalone Executor receives
    # ``NodeInput`` envelopes for downstream nodes with empty
    # ``task_id`` — there is no Task entity in the repo, ``GET
    # /tasks/{id}`` 404s, and the projector's task view is missing
    # entries. The tee fires synchronously inside ``publish`` so the
    # Task is in the repo BEFORE the broker delivers ``NodeInput`` to
    # the Executor pod.
    #
    # Default ``in_process`` keeps the engine's inline task creation —
    # wiring the consumer there would double-emit ``TaskDispatched``.
    from app.layer4_frameworks.executor import (
        RunCompletionConsumer,
        RunCompletionTeeingPublisher,
        RunStartConsumer,
        RunStartTeeingPublisher,
        TaskCreatorConsumer,
        TaskCreatorTeeingPublisher,
    )
    _exec_mode = (
        getattr(settings, "EXECUTOR_MODE", "in_process") or "in_process"
    ).lower()
    if _exec_mode != "in_process":
        task_creator_consumer = TaskCreatorConsumer(
            task_repo=repos.task,
            workflow_repo=repos.workflow,
            run_repo=repos.run,
            task_factory=task_factory,
        )
        event_publisher = TaskCreatorTeeingPublisher(
            inner=event_publisher,
            consumer=task_creator_consumer,
        )
        # Late-bind the publisher reference so the consumer's
        # ``TaskDispatched`` re-publish hits the same outer chain.
        task_creator_consumer.set_publisher(event_publisher)

        # Phase 4 Sub 4.2 — RunStartConsumer.
        #
        # Under EXECUTOR_MODE=executor the engine's
        # ``handle_entry_nodes`` is skipped (see
        # ``_is_event_driven_run_start``); the consumer drives
        # entry-node dispatch by emitting ``NodeInput`` per entry
        # whenever ``RunStarted`` or ``ChildRunStarted`` is published.
        # Wired in BOTH ``shadow`` and ``executor`` for consistency,
        # but engine gating only takes effect under ``executor``.
        run_start_consumer = RunStartConsumer(
            run_repo=repos.run,
            workflow_repo=repos.workflow,
        )
        event_publisher = RunStartTeeingPublisher(
            inner=event_publisher,
            consumer=run_start_consumer,
        )
        run_start_consumer.set_publisher(event_publisher)

        # Executor-mode completion. Only under ``executor`` does the
        # engine bow out of handler invocation AND its inline
        # ``check_run_completion``; in ``shadow`` the engine still drives
        # completion, so wiring this there would only add redundant
        # (idempotent) completion checks. The consumer enqueues a
        # ``CheckRunCompletionAction`` on each ``NodeCompleted`` /
        # ``NodeFailed``; the queue is late-bound below once it exists.
        if _exec_mode == "executor":
            run_completion_consumer = RunCompletionConsumer()
            event_publisher = RunCompletionTeeingPublisher(
                inner=event_publisher,
                consumer=run_completion_consumer,
            )
        else:
            run_completion_consumer = None
    else:
        task_creator_consumer = None
        run_start_consumer = None
        run_completion_consumer = None

    # Bind the dispatcher's publisher — mode-dependent.
    #
    # ``executor``: the engine bows out of task creation, so the
    # dispatcher's fan-out ``NodeInput`` for downstream nodes MUST reach
    # the ``TaskCreatorConsumer`` to get a Task entity. Route it through
    # the FINAL outer-most chain (TaskCreator/RunStart/RunCompletion tees).
    #
    # ``in_process`` / ``shadow``: the engine still creates downstream
    # tasks inline. Routing the dispatcher through the outer tees there
    # would make the ``TaskCreatorConsumer`` re-announce an already-
    # engine-created task (a spurious second ``NodeDispatched``), which
    # breaks event-shape expectations. Keep the dispatcher on its partial
    # ``DispatcherTeeingPublisher`` — the original behaviour.
    if _exec_mode == "executor":
        dispatcher_consumer.set_publisher(event_publisher)
    else:
        dispatcher_consumer.set_publisher(dispatcher_tee_publisher)

    successor_processor = SuccessorProcessor(
        handler_registry,
        task_factory,
        data_store_service=data_store_service,
        monitor=monitor,
        run_context_view_store=run_context_view_store,
    )

    # Phase 4 Sub 4.3 — event-driven loop driver is the only path.
    # The legacy imperative ``_run_loop_iterations`` walker has been
    # retired (its body is now a thin enqueue-and-return shim that
    # hands control to the queue worker, which advances iterations
    # via ``maybe_enqueue_loop_iteration`` → ``LoopIterationAction``
    # → ``orchestrate_loop_iteration``). The ``EVENT_DRIVEN_LOOP``
    # config flag is retained for backward-compatible ``.env`` files
    # but is no longer consulted; the driver is always wired.
    from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
    event_driven_loop_driver = EventDrivenLoopDriver(
        resolver,
        run_context_view_store=run_context_view_store,
        data_store_service=data_store_service,
    )

    # ResolutionScope assembler — single source of truth for scope
    # frame ordering. The engine builds a scope per run (via this
    # builder) inside ``_enter_run_scope`` so the resolver overlay can
    # answer ``$parent`` / ``$root`` / ``$variable`` and per-iteration
    # ``$loop`` lookups without the 13 handler call sites changing.
    # ``materialize`` lets ``UserVariablesFrame`` fetch INPUT-node scalar
    # contributions for ``$variable.X`` on demand (no-``extracted`` OData
    # model) instead of reading a pre-materialised inline cache.
    resolution_scope_builder = ResolutionScopeBuilder(
        materialize=data_store_service.materialize,
        credential_cipher=credential_cipher,
        # Lazy half of ``$credentials`` — awaited only if an expression asks.
        # Same provider the TASK handler uses, so an expression and the node's
        # own worker can never disagree about what a ref resolves to.
        resolve_credentials=lambda run, workflow: credential_provider.for_refs(
            collect_refs(workflow), run,
        ),
    )

    # ``node_output_repo`` is built at the top of this function (beside the
    # resolver's schema dictionary) — ONE instance shared by the engine's mirror
    # (the single writer, which is what gave builtins their rows) and every
    # reader. A second construction here would be harmless today and a trap
    # tomorrow, the moment the repo holds any state.
    orchestration_engine = OrchestrationEngine(
        handler_registry=handler_registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=repos.task,
        run_repo=repos.run,
        event_publisher=event_publisher,
        callback_publisher=infra.messaging.callback_publisher,
        file_service_client=file_service_client,
        csv_converter=csv_converter,
        workflow_repo=repos.workflow,
        workflow_snapshot_repo=repos.workflow_snapshot,
        variable_resolver=resolver,
        data_store_service=data_store_service,
        monitor=monitor,
        event_driven_loop_driver=event_driven_loop_driver,
        max_concurrent_iterations_per_run=settings.MAX_CONCURRENT_ITERATIONS_PER_RUN,
        run_context_view_store=run_context_view_store,
        scope_builder=resolution_scope_builder,
        executor_mode=(
            getattr(settings, "EXECUTOR_MODE", "in_process") or "in_process"
        ).lower(),
        node_output_repo=node_output_repo,
        environment_repo=repos.environment,  # SA-2365: per-subflow env binding
        # SA-2496: guard a DYNAMIC-resolved child env id against the target
        # subflow's SA-2450 assignment.
        workflow_environment_repo=repos.workflow_environment,
        # SA-2605: guard a dynamic child env against the sub-flow's SECTION membership.
        workflow_section_repo=repos.workflow_section,
        section_membership_repo=repos.section_membership,
    )

    # 3b. Orchestration queue (async decoupling)
    # infra.broker is the shared broker client (Kafka or Event Mesh); None in mock mode.
    if infra.broker is not None:
        from app.layer4_frameworks.orchestration_queue.kafka_queue import BrokerOrchestrationQueue
        orchestration_queue = BrokerOrchestrationQueue(infra.broker, monitor=monitor)
    else:
        from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
        orchestration_queue = MemoryOrchestrationQueue()

    # Every CAS writer of the run aggregate takes this run's lock before it
    # touches the row. Wired HERE rather than in main.py so the container tests
    # get the same serialisation the service does — the defect this closes was
    # invisible to a suite whose drivers dispatch one action at a time.
    #
    # Module-global, like ``set_monitor`` beside it. A process that builds two
    # containers (the cross-instance tests) leaves the provider pointing at the
    # LAST engine, so the first container's writers take locks that serialise
    # nothing. That is the pre-fix behaviour, not a new hazard — and it cannot
    # deadlock, because ``_held_run_locks`` is one ContextVar shared by every
    # engine, so a nested write still short-circuits.
    from app.layer2_application import run_save_retry as _cas_retry
    _cas_retry.set_lock_provider(orchestration_engine.locked_run_for_write)

    from app.layer4_frameworks.orchestration_queue.worker import OrchestrationWorker
    from app.layer4_frameworks.orchestration_queue.response_registry import ResponseRegistry
    orchestration_engine.set_queue(orchestration_queue)
    # Late-bind the queue to the executor-mode completion consumer — it
    # was wired into the publisher chain above, before the queue existed.
    if run_completion_consumer is not None:
        run_completion_consumer.set_queue(orchestration_queue)

    # Phase M.3.2: per-instance id for cross-instance response routing.
    # Override with INSTANCE_ID env var in test harnesses that run two
    # bootstraps against a shared broker stub.
    #
    # SA-2087 (R12): the id only has to be UNIQUE for routing to be correct
    # (it is hashed into the response-topic name), because the futures it
    # routes to live in this process and die with it — a restarted pod has
    # nothing left to resolve. STABILITY buys something different: a bounded
    # set of broker queues: a random id mints one per BOOT, a stable id one
    # per REPLICA.
    #
    # SA-2087 pinned that stability in the CF manifest. Identity is resolved
    # in-process now because the manifest could not deliver it: the pin landed
    # on the executor manifest only (this app kept the random default and
    # leaked a queue per deploy), and its ``${CF_INSTANCE_INDEX}`` is never
    # expanded — CF does not interpolate manifest env values. ``resolve_instance_id``
    # derives the same <app>_<index> from the CF container env, so both apps
    # get a bounded identity with nothing to substitute at deploy time.
    instance_id = resolve_instance_id(settings.INSTANCE_ID)
    orchestration_response_registry = ResponseRegistry()

    # 3c. Read-side view store was built earlier (before
    # ``successor_processor``) for Phase 2a so engine reads go through
    # ``IRunContextViewStore``. Phase 3b.3 collapsed the former
    # tri-state selection into always wiring
    # ``CachedProjectorRunContextViewStore``.

    # 3d. v2 node-catalog providers + cache (Phase 1).
    # STRICT_CONTRACT_VALIDATION=true → reject workers/business definitions
    # with malformed FormField schemas; default lenient drops bad fields
    # with a warning so one broken worker doesn't break the palette.
    strict_contracts = getattr(settings, "STRICT_CONTRACT_VALIDATION", False)
    builtin_node_provider = BuiltinNodeProvider()
    worker_node_provider = WorkerNodeProvider(
        client=infra.messaging.worker_registry_client,
        strict_schema_validation=strict_contracts,
        stale_after_seconds=settings.WORKER_STALE_AFTER_SECONDS,  # SA-2052 (R5)
    )
    business_node_provider = BusinessNodeProvider(
        repo=repos.node_definition,
        resolver=node_definition_resolver,
        strict_schema_validation=strict_contracts,
    )
    node_catalog_cache = NodeCatalogCache(ttl_seconds=30)

    # Phase 3 Sub 3.2 / 3.3 / 3.4 — outbox infrastructure.
    #
    # Constructed BEFORE the smart-DI dependency pool so use cases that
    # opt in (DATA_EDIT under ``OUTBOX_DATA_EDIT=on``, HITL resume under
    # ``OUTBOX_HITL_RESUME=on``) can pick them up via signature-based
    # injection. Handler map is populated conditionally — the
    # dispatcher idles when both flags are off (empty handler map +
    # nobody emits ``OutboxRequested``).
    from app.layer2_application.events.outbox_awaiter import OutboxAwaiter
    from app.layer4_frameworks.outbox.handlers import (
        FileDeleteHandler,
        FileUploadHandler,
        QueueEnqueueHandler,
        StageRecordHandler,
    )
    from app.layer4_frameworks.outbox.outbox_bytes_channel import (
        OutboxBytesChannel,
    )
    from app.layer4_frameworks.outbox.outbox_dispatcher import OutboxDispatcher
    from app.layer4_frameworks.persistence.memory.memory_outbox_repository import (
        MemoryOutboxRepository,
    )

    outbox_repo = MemoryOutboxRepository(event_log_repo)
    outbox_bytes_channel = OutboxBytesChannel()
    outbox_awaiter = OutboxAwaiter()

    outbox_handlers: dict = {}
    if getattr(settings, "OUTBOX_DATA_EDIT", False):
        outbox_handlers["file_upload"] = FileUploadHandler(
            file_service=file_service_client,
            bytes_channel=outbox_bytes_channel,
        )
        outbox_handlers["file_delete"] = FileDeleteHandler(
            file_service=file_service_client,
        )
    if getattr(settings, "OUTBOX_HITL_RESUME", False):
        outbox_handlers["stage_record"] = StageRecordHandler(
            stage_recorder=stage_recorder,
        )
        outbox_handlers["queue_enqueue"] = QueueEnqueueHandler(
            queue=orchestration_queue,
        )

    outbox_dispatcher = OutboxDispatcher(
        outbox_repo=outbox_repo,
        event_log_repo=event_log_repo,
        handlers=outbox_handlers,
        poll_interval_s=float(
            getattr(settings, "OUTBOX_DISPATCHER_POLL_INTERVAL_S", 1.0),
        ),
        max_attempts=int(getattr(settings, "OUTBOX_MAX_ATTEMPTS", 5)),
        awaiter=outbox_awaiter,
    )

    # Durable timers (SA-1371). Built BEFORE the smart-DI pool so the
    # create_timer / cancel_timer use cases pick up ``timer_repo`` via
    # signature-based injection. The dispatcher + single-owner lease are
    # gated on ``TIMER_DISPATCHER_ENABLED`` — dormant (never leader) when
    # off, so it is safe to construct unconditionally and let main.py's
    # lifespan decide whether to run the loop.
    from app.layer4_frameworks.timer.single_owner_lease import SingleOwnerLease
    from app.layer4_frameworks.timer.timer_dispatcher import TimerDispatcher

    timer_repo = _build_timer_repo(settings, infra=infra)
    worker_config_repo = _build_worker_config_repo(settings, infra=infra)  # SA-2437
    timer_dispatcher_enabled = bool(
        getattr(settings, "TIMER_DISPATCHER_ENABLED", False),
    )
    timer_lease = SingleOwnerLease(enabled=timer_dispatcher_enabled)
    timer_dispatcher = TimerDispatcher(
        timer_repo=timer_repo,
        event_publisher=event_publisher,
        lease=timer_lease,
        poll_interval_s=float(
            getattr(settings, "TIMER_DISPATCHER_POLL_INTERVAL_S", 1.0),
        ),
    )

    # 4. Available dependency pool
    # Six scope providers — ordering set by each provider's ``order`` class
    # constant (run=10, workflow=20, variables=30, input=40, node=50,
    # artifacts=60). The use case sorts them at __init__ time.
    from app.layer2_application.features.get_variable_suggestions.use_cases.scope_providers import (
        ArtifactsScopeProvider,
        CredentialsScopeProvider,
        EnvScopeProvider,
        InputScopeProvider,
        NodeScopeProvider,
        RunScopeProvider,
        VariablesScopeProvider,
        WorkflowScopeProvider,
    )
    # ``node_output_repo`` / ``_node_output_db`` are built ABOVE, before the
    # orchestration engine, which now owns the only write path into
    # AIW_RUN_NODE_OUTPUT. The scope providers and read use cases below share
    # that same instance.
    # Loop-state store — AIW_RUN_LOOP_STATE (migrations 056 / 057). The MIRROR
    # is wired into HanaRunRepository (persistence/__init__), because save() is
    # the only place context.loop_state reaches the database. This instance
    # exists for the two use cases that CLEAR loop_state: the mirror is gated on
    # a non-empty dict so it can never propagate a clear, and a row left behind
    # reads as a loop that is still running.
    loop_state_repo = None
    if _node_output_db is not None:
        from app.layer4_frameworks.persistence.hana.hana_loop_state_repo import (
            HanaLoopStateRepository,
        )
        # The dictionary is what keeps the stored collection pointer from
        # scaling with the user's column count (392 B + 40 B x columns becomes
        # 392 B + 0.2 B x columns). Passing it also ARMS the thin write — the
        # repo refuses to store a hash it could not read back.
        loop_state_repo = HanaLoopStateRepository(
            _node_output_db, schema_dictionary=_schema_dictionary,
        )
    node_output_read_mode = str(
        getattr(settings, "NODE_OUTPUT_READ_MODE", "event") or "event",
    )
    _logger.info(
        "Node-output store: %s | read mode: %s",
        "hana" if node_output_repo else "none (writes skipped)",
        node_output_read_mode,
    )

    scope_providers = [
        RunScopeProvider(),
        WorkflowScopeProvider(),
        CredentialsScopeProvider(),
        VariablesScopeProvider(),
        EnvScopeProvider(),
        InputScopeProvider(),
        NodeScopeProvider(
            run_repo=repos.run, workflow_repo=repos.workflow,
            file_service_client=file_service_client,
            run_context_view_store=run_context_view_store,
            logger=log,
            node_output_repo=node_output_repo,
            node_output_read_mode=node_output_read_mode,
        ),
        ArtifactsScopeProvider(
            run_repo=repos.run, workflow_repo=repos.workflow,
            file_service_client=file_service_client,
            run_context_view_store=run_context_view_store,
            logger=log,
        ),
    ]

    available_dependencies = {
        "logger": log,
        "monitor": monitor,
        "app_config": settings,
        "workflow_repo": repos.workflow,
        "workflow_snapshot_repo": repos.workflow_snapshot,
        "run_repo": repos.run,
        "task_repo": repos.task,
        "stage_history_repo": repos.stage_history,
        "event_publisher": event_publisher,
        "stage_recorder": stage_recorder,
        "orchestration_engine": orchestration_engine,
        "handler_registry": handler_registry,
        "variable_resolver": resolver,
        "callback_publisher": infra.messaging.callback_publisher,
        "worker_registry_client": infra.messaging.worker_registry_client,
        "node_definition_repo": repos.node_definition,
        "webhook_repo": repos.webhook,
        "node_definition_resolver": node_definition_resolver,
        "file_service_client": file_service_client,
        "governance_client": governance_client,
        "data_factory_client": data_factory_client,
        "node_data_factory_service": node_data_factory_service,
        "data_store_service": data_store_service,
        "schema_transform_client": schema_transform_client,
        "orchestration_queue": orchestration_queue,
        "run_context_view_store": run_context_view_store,
        "event_log_repo": event_log_repo,
        "timer_repo": timer_repo,
        "worker_config_repo": worker_config_repo,  # SA-2437
        "snapshot_repo": snapshot_repo,
        "node_input_etag_calculator": node_input_etag_calculator,
        "row_path_resolver": row_path_resolver,
        "builtin_node_provider": builtin_node_provider,
        "worker_node_provider": worker_node_provider,
        "business_node_provider": business_node_provider,
        "node_catalog_cache": node_catalog_cache,
        "scope_providers": scope_providers,
        "gateway_app_repo": repos.gateway_app,
        "gateway_function_repo": repos.gateway_function,
        "environment_repo": repos.environment,
        "env_section_schema_repo": repos.env_section_schema,  # SA-2471
        "workflow_environment_repo": repos.workflow_environment,  # SA-2450
        "workflow_section_repo": repos.workflow_section,  # SA-2605
        "section_membership_repo": repos.section_membership,  # SA-2605
        # SA-2570: users come from Keycloak (central identity), not a local mirror.
        "keycloak_admin_client": KeycloakAdminClient(
            issuer=settings.KEYCLOAK_ISSUER,
            client_id=settings.KEYCLOAK_CLIENT_ID,
            client_secret=settings.KEYCLOAK_CLIENT_SECRET,
        ),
        "credential_cipher": credential_cipher,
        "vault_credential_client": vault_credential_client,
        "http_probe": _http_probe_callable(),
    }
    # Phase 3 Sub 3.3 / 3.4 — outbox migrations: inject the awaiter
    # + bytes channel + repo whenever either feature is enabled. Use
    # cases see ``None`` in their kwargs otherwise and take the legacy
    # direct path. Dispatcher gets the awaiter regardless (single-pod
    # tee).
    if (
        getattr(settings, "OUTBOX_DATA_EDIT", False)
        or getattr(settings, "OUTBOX_HITL_RESUME", False)
    ):
        available_dependencies["outbox_awaiter"] = outbox_awaiter
        available_dependencies["outbox_bytes_channel"] = outbox_bytes_channel
        available_dependencies["outbox_repo"] = outbox_repo
        available_dependencies["outbox_http_wait_timeout_s"] = float(
            getattr(settings, "OUTBOX_HTTP_WAIT_TIMEOUT_S", 5.0),
        )
    # Node-output store + read mode are built above (the scope providers need
    # them first). Publish them by name so the smart injector reaches every use
    # case that declares the parameter — CompleteTaskUseCase for the write,
    # GetNodeOutputUseCase for the read.
    if node_output_repo is not None:
        available_dependencies["node_output_repo"] = node_output_repo
    available_dependencies["node_output_read_mode"] = node_output_read_mode
    # Reaches ResetTestRunUseCase and TestFullWorkflowUseCase — the only two
    # callers of ``loop_state.clear()``.
    if loop_state_repo is not None:
        available_dependencies["loop_state_repo"] = loop_state_repo

    # Filter out None values
    available_dependencies = {k: v for k, v in available_dependencies.items() if v is not None}

    # 4. Auto-discover features and smart-inject dependencies
    load_features()
    container = {}
    for name, UseCaseClass in FEATURE_REGISTRY.items():
        sig = inspect.signature(UseCaseClass)
        required = {
            p for p, v in sig.parameters.items()
            if p not in ("self", "kwargs")
            and v.default is inspect.Parameter.empty
        }
        missing = required - set(available_dependencies.keys())
        if missing:
            _logger.warning("Feature '%s' missing dependencies: %s — skipping", name, missing)
            continue
        injectable = {p: d for p, d in available_dependencies.items() if p in sig.parameters}
        container[name] = UseCaseClass(**injectable)

    # Post-wire: give the WAIT handler its CreateTimerUseCase (SA-1371 S4).
    # The handler is registered in _build_handler_registry (line ~590) before
    # the create_timer use case exists — it depends on the fully-teed
    # event_publisher + timer_repo wired later — so inject it here now that the
    # feature container is built.
    if "create_timer" in container:
        try:
            wait_handler = handler_registry.get(NodeType.WAIT)
        except Exception:
            wait_handler = None
        if wait_handler is not None and hasattr(wait_handler, "_create_timer"):
            wait_handler._create_timer = container["create_timer"]

    # Post-wire: give the MERGE handler the view store (spec D). Registered in
    # _build_handler_registry before the store exists, same ordering as the WAIT
    # handler above. Without it the handler reads completed_nodes / skipped_nodes
    # off an aggregate DROP CONTEXT emptied, its quorum buffer is ephemeral, and
    # a two-predecessor ``all`` merge never fires.
    try:
        merge_handler = handler_registry.get(NodeType.MERGE)
    except Exception:
        merge_handler = None
    if merge_handler is not None and hasattr(merge_handler, "_view_store"):
        merge_handler._view_store = run_context_view_store

    # Post-wire: give edit_node_data a reference to the rerun_task use case
    # for its rerun-dispatch path. Optional — the use case handles ``None``
    # gracefully by returning rerun.triggered=false.
    if "edit_node_data" in container and "rerun_task" in container:
        container["edit_node_data"]._rerun_task = container["rerun_task"]

    # Post-wire: same for rerun_run, which delegates to rerun_task per anchor.
    if "rerun_run" in container and "rerun_task" in container:
        container["rerun_run"]._rerun_task = container["rerun_task"]

    # Post-wire: submit_node delegates to resume_pending_task. The default
    # constructor instantiates its own delegate when the shared one isn't
    # injected; prefer the bootstrap-managed singleton.
    if "submit_node" in container and "resume_pending_task" in container:
        container["submit_node"]._delegate = container["resume_pending_task"]

    # Post-wire: resume_run fans out PENDING tasks via resume_pending_task.
    if "resume_run" in container and "resume_pending_task" in container:
        container["resume_run"]._resume_pending_task_usecase = container[
            "resume_pending_task"
        ]

    # Post-wire: batch delegates to the single-detail use case per ref.
    if (
        "get_node_catalog_details_batch" in container
        and "get_node_catalog_detail" in container
    ):
        container["get_node_catalog_details_batch"]._detail_uc = container[
            "get_node_catalog_detail"
        ]

    # Post-wire: the liveness sweep fails unservable tasks through the canonical
    # worker-failure path (FailTaskUseCase can't be constructor-injected — it
    # isn't built yet at feature-discovery time).
    if (
        "sweep_unservable_dispatched_tasks" in container
        and "fail_task" in container
    ):
        container["sweep_unservable_dispatched_tasks"]._fail_task = container[
            "fail_task"
        ]

    # Post-wire: give the worker-registry mirror use case its HANA repo. The repo
    # is NOT in available_dependencies (it depends on infra.db, built earlier),
    # so it's injected here now that the feature container exists. In mock mode
    # (no HANA) the factory returns None and the use case no-ops (SA-2050 R3).
    if "worker_mirror_reconcile" in container:
        container["worker_mirror_reconcile"]._repo = _build_worker_mirror_repo(
            settings, infra,
        )

    # R9 (SA-2056): the health-feed use case publishes directly to the broker, which
    # is not in available_dependencies — cross-wire it here (mirror of the R3 _repo
    # wire above). No-op safe when infra.broker is None (INFRA_MODE=mock).
    if "worker_health_feed" in container:
        container["worker_health_feed"]._broker = infra.broker

    # Expose the same file_service_client used by the engine (shared in-memory store)
    container["file_service_client"] = file_service_client

    # Internal functions use cases (manual registration - not auto-discovered)
    if "start_run" in container:
        from app.layer2_application.features.internal_functions import (
            InternalTriggerWorkflowUseCase,
            InternalCallApiUseCase,
        )
        container["internal_trigger_workflow"] = InternalTriggerWorkflowUseCase(
            logger=_logger,
            start_run_usecase=container["start_run"],
            file_service_client=file_service_client,
        )
        container["internal_call_api"] = InternalCallApiUseCase(
            logger=_logger,
            file_service_client=file_service_client,
        )

    # Schedule (crontab) trigger scheduler (manual registration - gated, NOT
    # auto-discovered). Fires runs for schedule-typed TRIGGER nodes. Firing is
    # not idempotent across instances, so enable on EXACTLY ONE instance.
    # See docs/design/schedule-trigger-scheduler.md.
    if settings.WORKFLOW_SCHEDULER_ENABLED and "start_run" in container:
        from app.layer2_application.features.fire_due_schedules.use_cases.fire_due_schedules_usecase import (
            FireDueSchedulesUseCase,
        )
        container["fire_due_schedules"] = FireDueSchedulesUseCase(
            logger=log,
            workflow_repo=repos.workflow,
            start_run=container["start_run"],
            monitor=monitor,
        )
        _logger.info("Schedule trigger scheduler ENABLED (tick=%ds)", settings.SCHEDULER_TICK_SECONDS)

    # Approval process use case (manual registration - fetches from governance API)
    # Reuses the governance_client already created earlier for the handler registry.
    if governance_client is not None:
        from app.layer2_application.features.get_approval_process import GetApprovalProcessUseCase
        from app.layer2_application.read.approval_process_transformer import ApprovalProcessTransformer

        container["get_approval_process_usecase"] = GetApprovalProcessUseCase(
            governance_client=governance_client,
            transformer=ApprovalProcessTransformer(),
        )
        _logger.info("Approval process use case registered (url=%s)", governance_api_url)
    else:
        _logger.info("GOVERNANCE_API_URL not configured — approval process endpoint will return 500")

    # Phase 4b.7: expose the event log repository on the container so
    # operators and tests can inspect which backend is wired. The same
    # instance is injected into features via the smart-DI dependency
    # pool above.
    container["event_log_repo"] = event_log_repo

    # Phase 4c.3: expose the read-side view store so operators and
    # tests can confirm whether the projector flip is active. Same
    # pattern as ``event_log_repo`` above — the store is ALSO in the
    # smart-DI pool where feature use cases pick it up, but direct
    # container access is needed for health checks and bootstrap tests.
    container["run_context_view_store"] = run_context_view_store

    # Phase 2b — dispatcher shadow: None unless DISPATCHER_MODE=shadow.
    container["dispatcher_consumer"] = dispatcher_consumer
    container["dispatcher_parity_comparator"] = dispatcher_parity_comparator

    # Store infrastructure for shutdown access
    container["_governance_client"] = governance_client
    container["_approval_transformer"] = ApprovalProcessTransformer()
    container["_infra"] = infra
    container["_worker_registry_client"] = infra.messaging.worker_registry_client
    # SA-1551: expose the vault client so the worker-function invoke proxy can
    # resolve a design-time credential_ref into __credentials (same resolver as
    # StartRun). None when the vault is unconfigured — the proxy stays dormant.
    container["_vault_credential_client"] = vault_credential_client
    # SA-2368: expose the provider so the wiring itself is testable. Whether its
    # cache is run-scoped decides how long a decrypted secret stays in RAM, and
    # that is a property of THIS composition root, not of the cache class — a
    # plain BoundedTTLCache here would silently restore the 3.9-day residency.
    container["_credential_provider"] = credential_provider
    # Expose monitor so the /metrics endpoint can reach its registry.
    container["monitor"] = monitor
    # SA-2059: expose the workflow + run repos by name so the
    # EventTriggerConsumer can route (list_trigger_routes) and dedup
    # (run existence check) at handle time.
    container["workflow_repo"] = repos.workflow
    container["run_repo"] = repos.run

    # SA-2059 B7/B8: inbound webhook registrations + convergent ingress.
    # The registration store is memory-backed (as webhooks are today, even in
    # HANA mode); the ingress service verifies + normalizes + publishes each
    # inbound POST onto the registration's emit_topic via the broker — never a
    # direct StartRun (Pattern 1 retired).
    # F2: registrations + deliveries were memory-only (lost on restart). Pick the
    # backend the same way _build_timer_repo does — WEBHOOK_BACKEND=hana shares
    # the HanaConnectionManager at infra.db; fall back to memory with a warning
    # when no HANA connection is available so a misconfig never fails startup.
    from app.layer4_frameworks.persistence.memory.memory_webhook_registration_repo import (
        MemoryWebhookRegistrationRepository,
    )
    _webhook_backend = (getattr(settings, "WEBHOOK_BACKEND", "memory") or "memory").lower()
    _webhook_hana_db = getattr(infra, "db", None) if infra is not None else None
    if _webhook_backend == "hana" and _webhook_hana_db is not None:
        from app.layer4_frameworks.persistence.hana.hana_webhook_registration_repo import (
            HanaWebhookRegistrationRepository,
        )
        _logger.info("Webhook registration backend: hana (shared HanaConnectionManager from infra.db)")
        webhook_registration_repo = HanaWebhookRegistrationRepository(_webhook_hana_db)
    else:
        if _webhook_backend == "hana":
            _logger.warning(
                "WEBHOOK_BACKEND=hana but no HANA connection is available "
                "(infra.db is None — check INFRA_MODE and HANA_* settings). "
                "Falling back to in-memory webhook registration store."
            )
        webhook_registration_repo = MemoryWebhookRegistrationRepository()
    # Registration CRUD (Settings → Triggers) stays available even when the
    # ingress is dormant, so operators can pre-register sources before cutover.
    container["webhook_registration_repo"] = webhook_registration_repo
    # F3: the /webhooks event ingress and the EventTriggerConsumer are ONE
    # feature — gate them on the SAME flag. Publishing without the consumer would
    # drop events into a topic nobody reads, and retiring Pattern 1 (direct
    # webhook→StartRun) "on deploy" would break existing inbound webhooks. Tying
    # both to EVENT_TRIGGER_CONSUMER_ENABLED makes the cutover a deliberate flip:
    # default (off) preserves the pre-SA-2059 world; flip it once every source is
    # re-registered as a trigger registration.
    event_ingress_enabled = bool(getattr(settings, "EVENT_TRIGGER_CONSUMER_ENABLED", False))
    if infra.broker is not None and event_ingress_enabled:
        from app.layer2_application.services.webhook_ingress.ingress_service import (
            WebhookIngressService,
        )
        from app.layer4_frameworks.messaging.event_trigger_consumer import (
            TRIGGERS_TOPIC,
        )
        # SA-2059: publish onto the consumer's single canonical inbox topic
        # (TRIGGERS_TOPIC), NOT the registration's emit_topic. emit_topic is the
        # LOGICAL routing key — it rides in envelope["event_topic"] and is matched
        # against each workflow's Event trigger by list_trigger_routes. The
        # physical broker topic is always the one shared queue the competing
        # EventTriggerConsumers drain. Publishing to emit_topic put events on a
        # topic nobody subscribed to → ingress returned 202 but no run ever ran.
        container["webhook_ingress_service"] = WebhookIngressService(
            webhook_registration_repo,
            lambda topic, message: infra.broker.publish_to_topic(TRIGGERS_TOPIC, message),
        )
    else:
        if infra.broker is not None and not event_ingress_enabled:
            _logger.info(
                "Webhook event ingress dormant: EVENT_TRIGGER_CONSUMER_ENABLED is off. "
                "POST /webhooks/* returns 503 until enabled; re-register legacy "
                "Pattern-1 webhooks as trigger registrations before flipping it."
            )
        container["webhook_ingress_service"] = None
    # Phase 3c: build the worker now that ``container["edit_node_data"]``
    # exists — PATCH re-routes through the queue with the worker holding
    # a ref to the use case + response registry so correlation-id → future
    # lookup resolves the HTTP caller.
    # Phase M.3.3: the worker also gets the broker client + our
    # instance_id so it can route responses cross-instance when Kafka
    # partition-by-run_id lands the action on a peer instance.
    # ORCHESTRATION_WORKER_COUNT controls how many worker coroutines drain
    # the orchestration queue on this pod. Default 4 is sized for 1 GB
    # control-plane pods; smaller pods should lower it, larger pods can
    # raise it. Same-run actions still serialise via the per-run
    # asyncio.Lock in OrchestrationEngine (orchestration_engine.py:159 / 580).
    worker_count = max(1, int(getattr(settings, "ORCHESTRATION_WORKER_COUNT", 1)))
    orchestration_workers = [
        OrchestrationWorker(
            orchestration_queue,
            orchestration_engine,
            edit_node_data_use_case=container.get("edit_node_data"),
            response_registry=orchestration_response_registry,
            instance_id=instance_id,
            broker_client=infra.broker,
        )
        for _ in range(worker_count)
    ]
    # Singular key retained for back-compat (tests / health checks read it).
    # New code should prefer ``_orchestration_workers`` (the list).
    container["_orchestration_worker"] = orchestration_workers[0]
    container["_orchestration_workers"] = orchestration_workers
    container["_orchestration_queue"] = orchestration_queue
    container["_orchestration_response_registry"] = orchestration_response_registry
    container["_instance_id"] = instance_id

    # BrokerRegistry — single configuration point for routing each
    # logical broker "purpose" to either primary or secondary. The
    # response_topics purpose targets the secondary broker when one
    # is configured (relieves primary quota pressure from per-instance
    # response queues); falls back to the primary when no secondary
    # binding is wired (single-broker deployments / mock mode).
    from app.layer4_frameworks.messaging.broker_registry import (
        BrokerRegistry,
        BrokerPurpose,
    )
    broker_registry = BrokerRegistry(
        primary=infra.broker,
        secondary=getattr(infra, "broker_secondary", None),
        purpose_routes={
            BrokerPurpose.RESPONSE_TOPICS: "secondary",
            BrokerPurpose.ORCHESTRATION: "primary",
            BrokerPurpose.REALTIME: "primary",
            BrokerPurpose.WORKFLOW_EVENT_QUEUE: "primary",
        },
    )
    container["_broker_registry"] = broker_registry

    # Phase M.3.3: per-instance response topic listener — only when a
    # broker is available for the response_topics purpose. In memory mode
    # both primary and secondary are None; the listener is skipped and
    # the worker + HTTP caller use the local-registry fast path.
    response_topics_broker = broker_registry.get(BrokerPurpose.RESPONSE_TOPICS)
    if response_topics_broker is not None:
        from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
            ResponseTopicListener,
        )
        response_topic_listener = ResponseTopicListener(
            broker_client=response_topics_broker,
            instance_id=instance_id,
            registry=orchestration_response_registry,
            monitor=monitor,  # SA-1466 — observable degradation
        )
        container["_response_topic_listener"] = response_topic_listener
    else:
        container["_response_topic_listener"] = None
    container["_push_gateway_consumer"] = push_gateway_consumer

    # Phase 3 Sub 3.2 / 3.3 / 3.4 — outbox infrastructure was constructed
    # earlier (before ``available_dependencies``) so use cases pick it up
    # via smart-DI. Expose the same instances on the container for tests
    # and the FastAPI lifespan task.
    container["outbox_repo"] = outbox_repo
    container["outbox_bytes_channel"] = outbox_bytes_channel
    container["outbox_awaiter"] = outbox_awaiter
    container["_outbox_dispatcher"] = outbox_dispatcher

    # Durable timers (SA-1371) — constructed earlier (before
    # ``available_dependencies``) so create_timer / cancel_timer pick up
    # ``timer_repo`` via smart-DI. Expose the store + lease + dispatcher on
    # the container for tests and the FastAPI lifespan task. The dispatcher
    # loop only starts when ``TIMER_DISPATCHER_ENABLED`` (main.py lifespan).
    container["timer_repo"] = timer_repo
    container["_timer_lease"] = timer_lease
    container["_timer_dispatcher"] = timer_dispatcher
    container["_timer_dispatcher_enabled"] = timer_dispatcher_enabled

    # Phase 3 Sub 3.5 — ExecutorConsumer.
    #
    # ``EXECUTOR_MODE=in_process`` (default): consumer constructed but
    # ``start()`` is a no-op; engine inline path is unchanged.
    # ``executor``: authoritative — Gate 3 follow-up surgery to delete
    # engine inline calls is required to reach drift=0; today the
    # mode is wired but the engine still emits authoritatively.
    from app.layer4_frameworks.executor import (
        ExecutorConsumer,
        ExecutorDedup,
        ExecutorMode,
    )
    executor_mode = (
        getattr(settings, "EXECUTOR_MODE", ExecutorMode.IN_PROCESS.value)
        or ExecutorMode.IN_PROCESS.value
    ).lower()
    if executor_mode not in (
        ExecutorMode.IN_PROCESS.value,
        ExecutorMode.EXECUTOR.value,
    ):
        _logger.warning(
            "EXECUTOR_MODE=%r not recognised, falling back to %r. "
            "Valid: in_process, executor.",
            executor_mode, ExecutorMode.IN_PROCESS.value,
        )
        executor_mode = ExecutorMode.IN_PROCESS.value

    executor_consumer = ExecutorConsumer(
        handler_registry=handler_registry,
        run_repo=repos.run,
        workflow_repo=repos.workflow,
        task_repo=repos.task,
        task_factory=task_factory,
        broker=infra.broker,
        dedup=ExecutorDedup(),
        mode=executor_mode,
        publisher=event_publisher,
    )
    container["_executor_consumer"] = executor_consumer
    container["_task_creator_consumer"] = task_creator_consumer
    container["_run_start_consumer"] = run_start_consumer
    container["_run_completion_consumer"] = run_completion_consumer

    # catalog.changed consumer — invalidates node_catalog_cache on worker
    # register/deregister events emitted by the executor.
    broker_client = getattr(infra, "broker", None) or getattr(
        getattr(infra, "messaging", None), "broker", None,
    )
    container["_catalog_changed_consumer"] = CatalogChangedConsumer(
        cache=node_catalog_cache,
        broker=broker_client,
        logger_impl=log,
    )

    # Worker results consumer — drives complete_task / fail_task from the
    # executor's ``workflow.results`` topic, replacing the synchronous HTTP
    # ``/complete`` callback. Only wired when the executor reports results
    # over the broker (``TRANSPORT_MODE=event_mesh``): in http/grpc transport
    # the executor reports via the synchronous result-reporter, so consuming
    # the broker too would double-process results (and FailTaskUseCase is not
    # idempotent). ``None`` when inactive so the lifespan start is a no-op.
    transport_mode = str(getattr(settings, "TRANSPORT_MODE", "http")).lower()
    if infra.broker is not None and transport_mode == "event_mesh":
        from app.layer4_frameworks.messaging.worker_results_consumer import (
            WorkerResultsConsumer,
        )
        container["_worker_results_consumer"] = WorkerResultsConsumer(
            container, infra.broker,
            handler_timeout_s=float(
                getattr(settings, "BROKER_HANDLER_TIMEOUT_S", 600.0)
            ),
        )
    else:
        container["_worker_results_consumer"] = None

    # Timer fired consumer (SA-1371 S4) — subscribes to ``timer.fired`` and
    # resumes a parked WAIT node when its durable timer fires, via resume_run.
    # Needs a broker to subscribe; gated on TIMER_RESUME_CONSUMER_ENABLED so it
    # is a no-op when disabled (``None`` → lifespan start is skipped).
    if infra.broker is not None and bool(
        getattr(settings, "TIMER_RESUME_CONSUMER_ENABLED", False)
    ):
        from app.layer4_frameworks.timer.timer_fired_consumer import (
            TimerFiredConsumer,
        )
        container["_timer_fired_consumer"] = TimerFiredConsumer(
            container, infra.broker,
        )
    else:
        container["_timer_fired_consumer"] = None

    # Event triggers (SA-2059) — EventTriggerConsumer subscribes to
    # ``workflow.triggers`` and starts a run for every workflow whose Event
    # trigger matches the topic (1→N), deduped by a derived run id. Needs a
    # broker; gated on EVENT_TRIGGER_CONSUMER_ENABLED so it is a no-op when
    # disabled (``None`` → lifespan start is skipped). NO leader gate —
    # competing consumers on the shared queue make it multi-instance safe.
    if infra.broker is not None and bool(
        getattr(settings, "EVENT_TRIGGER_CONSUMER_ENABLED", False)
    ):
        from app.layer4_frameworks.messaging.event_trigger_consumer import (
            EventTriggerConsumer,
        )
        container["_event_trigger_consumer"] = EventTriggerConsumer(
            container,
            infra.broker,
            condition_evaluator=condition_evaluator,
            max_starts_per_minute=int(
                getattr(settings, "EVENT_TRIGGER_MAX_STARTS_PER_MINUTE", 60)
            ),
            max_concurrent_starts=int(
                getattr(settings, "EVENT_TRIGGER_MAX_CONCURRENT_STARTS", 8)
            ),
        )
    else:
        container["_event_trigger_consumer"] = None

    # Smartsheet event trigger (smartsheet-event-trigger). A poller drains the
    # org-wide Event Reporting API and republishes each event onto the SAME
    # canonical topic the EventTriggerConsumer drains — so all of SA-2059's
    # routing/dedup/filter/admission is reused. Gated on a broker + the poller
    # flag + a token; publishing is pointless without the consumer, so warn if
    # that is off. ``None`` (any gate unmet) → lifespan start is skipped.
    _smartsheet_enabled = bool(getattr(settings, "SMARTSHEET_POLLER_ENABLED", False))
    _smartsheet_token = getattr(settings, "SMARTSHEET_TOKEN", "") or ""
    if infra.broker is not None and _smartsheet_enabled and _smartsheet_token:
        from datetime import datetime, timedelta, timezone
        from app.layer4_frameworks.messaging.event_trigger_consumer import TRIGGERS_TOPIC
        from app.layer4_frameworks.clients.smartsheet_events_client import (
            SmartsheetEventsClient,
        )
        from app.layer2_application.services.smartsheet_poller.normalizer import (
            make_normalizer,
        )
        from app.layer2_application.services.smartsheet_poller.poller import (
            SmartsheetPoller,
        )

        if not bool(getattr(settings, "EVENT_TRIGGER_CONSUMER_ENABLED", False)):
            _logger.warning(
                "SMARTSHEET_POLLER_ENABLED but EVENT_TRIGGER_CONSUMER_ENABLED is off — "
                "events will publish to workflow.triggers with nothing consuming them."
            )
        # Cursor store: reuse the webhook backend selection (hana when available,
        # else memory) — same _webhook_backend / _webhook_hana_db resolved above.
        if _webhook_backend == "hana" and _webhook_hana_db is not None:
            from app.layer4_frameworks.persistence.hana.hana_smartsheet_cursor_repo import (
                HanaSmartsheetCursorRepository,
            )
            _smartsheet_cursor_repo = HanaSmartsheetCursorRepository(_webhook_hana_db)
        else:
            from app.layer4_frameworks.persistence.memory.memory_smartsheet_cursor_repo import (
                MemorySmartsheetCursorRepository,
            )
            _smartsheet_cursor_repo = MemorySmartsheetCursorRepository()

        _ss_tenant = str(getattr(settings, "SMARTSHEET_TENANT_ID", "system"))
        _ss_lookback = int(getattr(settings, "SMARTSHEET_BOOTSTRAP_LOOKBACK_HOURS", 24))

        def _smartsheet_bootstrap_since() -> str:
            return (datetime.now(timezone.utc) - timedelta(hours=_ss_lookback)).isoformat()

        _smartsheet_client = SmartsheetEventsClient(_smartsheet_token)
        container["_smartsheet_poller"] = SmartsheetPoller(
            cursor_repo=_smartsheet_cursor_repo,
            fetch_page=_smartsheet_client.fetch_page,
            normalize=make_normalizer(tenant_id=_ss_tenant),
            publish=lambda env: infra.broker.publish_to_topic(TRIGGERS_TOPIC, env),
            bootstrap_since=_smartsheet_bootstrap_since,
            cursor_id=f"smartsheet:{_ss_tenant}",
        )
        _logger.info(
            "Smartsheet poller wired (tenant=%s, cursor backend=%s)",
            _ss_tenant, _webhook_backend if _webhook_hana_db is not None else "memory",
        )
    else:
        if _smartsheet_enabled and not _smartsheet_token:
            _logger.warning(
                "SMARTSHEET_POLLER_ENABLED but SMARTSHEET_TOKEN is empty — poller dormant."
            )
        container["_smartsheet_poller"] = None

    _logger.info("Loaded %d features: %s", len(container), sorted(container.keys()))
    return container
