"""Infrastructure layer — master factory."""
from __future__ import annotations

import logging as _logging
from dataclasses import dataclass
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.monitor_interface import IMonitor

from app.layer4_frameworks.persistence import RepositorySet, create_repositories
from app.layer4_frameworks.messaging import MessagingSet, create_messaging
from app.layer4_frameworks.logger import create_logger
from app.layer4_frameworks.monitoring import create_monitor


class _OverrideSettings:
    """Proxy that overrides specific attributes on a settings object."""

    def __init__(self, base: Any, **overrides: Any) -> None:
        self._base = base
        self._overrides = overrides

    def __getattr__(self, name: Any) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)


def _create_database_connection(settings: Any) -> Optional[object]:
    infra_mode = getattr(settings, "INFRA_MODE", "mock").lower()
    hana_host = getattr(settings, "HANA_HOST", "")

    if infra_mode in ["local","sap"] and hana_host:
        try:
            from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
                HanaConnectionManager,
            )
            from app.layer4_frameworks.persistence.hana.migrations import migrate
        except ImportError:
            _logging.getLogger(__name__).warning("hdbcli not available — falling back to mock mode")
            return None

        # ── Schema bootstrap toggle ──
        # When an external hdi-deploy task owns the schema (managed hdi-shared
        # container, which forbids runtime DDL), set HANA_RUN_MIGRATIONS=false so
        # the app issues no DDL. Default true keeps the self-migration path for
        # local / non-HDI deployments.
        run_migrations = getattr(settings, "HANA_RUN_MIGRATIONS", True)

        # ── Run migration with HDI credentials if available ──
        hdi_user = getattr(settings, "HANA_HDI_USERNAME", "")
        hdi_password = getattr(settings, "HANA_HDI_PASSWORD", "")

        if not run_migrations:
            _logging.getLogger(__name__).info(
                "HANA_RUN_MIGRATIONS=false — skipping runtime DDL "
                "(schema managed externally by the hdi-deploy task)."
            )
        elif hdi_user and hdi_password:
            _logging.getLogger(__name__).info(
                "Using HDI user '%s' for HANA migration", hdi_user
            )
            migration_settings = _OverrideSettings(
                settings,
                HANA_USERNAME=hdi_user,
                HANA_PASSWORD=hdi_password,
            )
            migration_manager = HanaConnectionManager(migration_settings)
            try:
                migration_manager.initialize()
                runtime_user = getattr(settings, "HANA_USERNAME", "")
                migrate(migration_manager, grant_to_user=runtime_user)
            except Exception as exc:
                _logging.getLogger(__name__).error(
                    "HANA migration with HDI user failed: %s", exc
                )
            finally:
                migration_manager.close()
        else:
            _logging.getLogger(__name__).warning(
                "No HDI credentials found (HANA_HDI_USERNAME is empty). "
                "Migration will use the regular runtime user."
            )

        # ── Create runtime connection pool ──
        try:
            manager = HanaConnectionManager(settings)
            manager.initialize()

            if run_migrations and not (hdi_user and hdi_password):
                migrate(manager)

            _logging.getLogger(__name__).info("Database connection: HANA (%s mode)", infra_mode)
            return manager
        except Exception as exc:
            _logging.getLogger(__name__).error("Failed to initialize HANA: %s — falling back to mock", exc)

    _logging.getLogger(__name__).info("Database connection: none (mock mode)")
    return None


def _create_message_broker_client(settings: Any, monitor: Optional[IMonitor] = None) -> Optional[object]:
    # Imported outside the try below: an explicit misconfiguration
    # (EventMeshConfigError) must FAIL boot, not fall back to mock (SA-1468).
    from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfigError

    messaging_mode = getattr(settings, "MESSAGING_MODE", "")
    infra_mode = (messaging_mode or getattr(settings, "INFRA_MODE", "mock")).lower()

    # Env-configured Event Mesh is primary: when the complete EVENT_MESH_*
    # credential set comes from environment variables (not VCAP), select
    # Event Mesh regardless of INFRA_MODE. An explicit MESSAGING_MODE remains
    # the escape hatch to force kafka/mock.
    if not messaging_mode and getattr(settings, "EVENT_MESH_CONFIG_SOURCE", "") == "env":
        if infra_mode not in ("event_mesh", "sap"):
            _logging.getLogger(__name__).info(
                "Event Mesh configured via environment — selecting it as primary broker "
                "(overrides infra mode %r)",
                infra_mode,
            )
        infra_mode = "event_mesh"

    if infra_mode == "local":
        try:
            from app.layer4_frameworks.messaging.kafka_broker_client import (
                KafkaBrokerClient,
            )

            client = KafkaBrokerClient(
                bootstrap_servers=getattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
                group_id=getattr(settings, "KAFKA_GROUP_ID", "ai-workflow"),
                auto_offset_reset=getattr(settings, "KAFKA_AUTO_OFFSET_RESET", "earliest"),
                # Was never passed, so it defaulted to None and
                # ``fut.result(timeout=None)`` blocked the poll thread forever
                # — directly under a comment claiming it was "bounded by
                # handler_timeout_s".
                handler_timeout_s=float(
                    getattr(settings, "BROKER_HANDLER_TIMEOUT_S", 600.0)
                ),
            )
            _logging.getLogger(__name__).info("Message broker client: Kafka (local mode)")
            return client
        except ImportError:
            _logging.getLogger(__name__).warning("confluent_kafka not available — falling back to mock")

    elif infra_mode in ("event_mesh", "sap"):
        try:
            from app.layer4_frameworks.messaging.event_mesh_broker_client import (
                EventMeshBrokerClient,
            )
            from app.layer4_frameworks.messaging.event_mesh_config import (
                EventMeshConfig,
            )

            config = EventMeshConfig(
                token_url=getattr(settings, "EVENT_MESH_TOKEN_URL", ""),
                messaging_url=getattr(settings, "EVENT_MESH_MESSAGING_URL", ""),
                management_url=getattr(settings, "EVENT_MESH_MANAGEMENT_URL", ""),
                client_id=getattr(settings, "EVENT_MESH_CLIENT_ID", ""),
                client_secret=getattr(settings, "EVENT_MESH_CLIENT_SECRET", ""),
                namespace=getattr(settings, "EVENT_MESH_NAMESPACE", "default"),
                require_namespace=bool(
                    getattr(settings, "EVENT_MESH_REQUIRE_NAMESPACE", False)
                ),
                allow_namespace_self_heal=bool(
                    getattr(settings, "EVENT_MESH_ALLOW_NAMESPACE_SELF_HEAL", True)
                ),
                timeout=float(getattr(settings, "EVENT_MESH_TIMEOUT_S", 30.0)),
                # Left at None until now — ``EventMeshConsumer`` waits on the
                # bridged coroutine with this value, so None meant "wait
                # forever". This is the tenant-1 path.
                handler_timeout_s=float(
                    getattr(settings, "BROKER_HANDLER_TIMEOUT_S", 600.0)
                ),
                max_retries=int(getattr(settings, "EVENT_MESH_MAX_RETRIES", 3)),
                max_payload_bytes=int(
                    getattr(settings, "EVENT_MESH_MAX_PAYLOAD_BYTES", 900_000)
                ),
                buffer_size=int(getattr(settings, "EVENT_MESH_BUFFER_SIZE", 10_000)),
                buffer_policy=str(getattr(settings, "EVENT_MESH_BUFFER_POLICY", "drop")),
                flush_batch_size=int(
                    getattr(settings, "EVENT_MESH_FLUSH_BATCH_SIZE", 50)
                ),
                flush_concurrency=int(
                    getattr(settings, "EVENT_MESH_FLUSH_CONCURRENCY", 10)
                ),
                max_connections=int(
                    getattr(settings, "EVENT_MESH_MAX_CONNECTIONS", 100)
                ),
                max_keepalive_connections=int(
                    getattr(settings, "EVENT_MESH_MAX_KEEPALIVE_CONNECTIONS", 20)
                ),
                circuit_failure_threshold=int(
                    getattr(settings, "EVENT_MESH_CIRCUIT_THRESHOLD", 5)
                ),
                circuit_recovery_timeout_s=float(
                    getattr(settings, "EVENT_MESH_CIRCUIT_RECOVERY_S", 30.0)
                ),
                enable_dlq=bool(
                    str(getattr(settings, "EVENT_MESH_DLQ_ENABLED", "1")).lower()
                    not in ("0", "false", "no")
                ),
                dlq_spool_dir=str(
                    getattr(settings, "EVENT_MESH_DLQ_SPOOL_DIR", "")
                ),
                dlq_best_effort_topics=str(
                    getattr(
                        settings,
                        "EVENT_MESH_DLQ_BEST_EFFORT_TOPICS",
                        "realtime.events",
                    )
                ),
                lock_duration_ms=int(
                    getattr(settings, "EVENT_MESH_LOCK_DURATION_MS", 60_000)
                ),
                dmq_enabled=str(
                    getattr(settings, "EVENT_MESH_DMQ_ENABLED", "1")
                ).lower() not in ("0", "false", "no", ""),
                dmq_suffix=str(getattr(settings, "EVENT_MESH_DMQ_SUFFIX", "-dmq")),
                max_redelivery_count=int(
                    getattr(settings, "EVENT_MESH_MAX_REDELIVERY_COUNT", 5)
                ),
                poll_interval_s=float(
                    getattr(settings, "EVENT_MESH_POLL_INTERVAL_S", 0.5)
                ),
                consume_transport=str(
                    getattr(settings, "EVENT_MESH_CONSUME_TRANSPORT", "amqp")
                ),
                consume_429_scope=str(
                    getattr(settings, "EVENT_MESH_429_COOLDOWN_SCOPE", "instance")
                ),
                consume_batch_max=int(
                    getattr(settings, "EVENT_MESH_CONSUME_BATCH_MAX", 10)
                ),
                amqp_url=str(getattr(settings, "EVENT_MESH_AMQP_URL", "")),
                amqp_ws_path=str(
                    getattr(settings, "EVENT_MESH_AMQP_WS_PATH", "/protocols/amqp10ws")
                ),
                amqp_prefetch=int(getattr(settings, "EVENT_MESH_AMQP_PREFETCH", 10)),
                amqp_concurrency=int(getattr(settings, "EVENT_MESH_AMQP_CONCURRENCY", 1)),
                runtime_fallback=bool(
                    getattr(settings, "EVENT_MESH_RUNTIME_FALLBACK", False)
                ),
                keepalive_expiry_s=float(
                    getattr(settings, "EVENT_MESH_KEEPALIVE_EXPIRY_S", 30.0)
                ),
                amqp_strip_namespace=str(
                    getattr(settings, "EVENT_MESH_AMQP_STRIP_NAMESPACE", "0")
                ).lower() not in ("0", "false", "no", ""),
            )
            client = EventMeshBrokerClient(config, monitor=monitor)
            _logging.getLogger(__name__).info(
                "Message broker client: Event Mesh (%s mode, lock_duration_ms=%d)",
                infra_mode,
                int(getattr(settings, "EVENT_MESH_LOCK_DURATION_MS", 60_000)),
            )
            return client
        except EventMeshConfigError:
            # Explicit misconfiguration: refuse to boot. Falling back to mock
            # here would silently disconnect the control plane from the mesh
            # in the exact environments that set the strict flag (SA-1468).
            _logging.getLogger(__name__).error(
                "Event Mesh configuration invalid — failing startup (no mock fallback)"
            )
            raise
        except Exception:
            _logging.getLogger(__name__).warning(
                "Failed to create Event Mesh broker client — falling back to mock"
            )

    _logging.getLogger(__name__).info("Message broker client: none (mock mode)")
    return None


def _create_secondary_broker_client(settings: Any, monitor: Any=None) -> Any:
    """Construct an optional secondary Event Mesh broker.

    Returns ``None`` unless ``EVENT_MESH_SECONDARY_*`` credentials are
    populated (typically via ``EVENT_MESH_SECONDARY_BINDING_NAME`` looking
    up a second SAP BTP Event Mesh service binding in VCAP_SERVICES).

    Only constructed when the primary broker is also Event Mesh — pairing
    a secondary with Kafka or mock doesn't make sense and is ignored.
    """
    infra_mode = getattr(settings, "INFRA_MODE", "mock") or "mock"
    if infra_mode not in ("event_mesh", "sap"):
        return None

    token_url = getattr(settings, "EVENT_MESH_SECONDARY_TOKEN_URL", "") or ""
    if not token_url:
        _logging.getLogger(__name__).debug(
            "No secondary Event Mesh credentials configured; "
            "skipping secondary broker construction"
        )
        return None

    try:
        from app.layer4_frameworks.messaging.event_mesh_broker_client import (
            EventMeshBrokerClient,
        )
        from app.layer4_frameworks.messaging.event_mesh_config import (
            EventMeshConfig,
        )

        config = EventMeshConfig(
            token_url=token_url,
            messaging_url=getattr(settings, "EVENT_MESH_SECONDARY_MESSAGING_URL", ""),
            management_url=getattr(settings, "EVENT_MESH_SECONDARY_MANAGEMENT_URL", ""),
            client_id=getattr(settings, "EVENT_MESH_SECONDARY_CLIENT_ID", ""),
            client_secret=getattr(settings, "EVENT_MESH_SECONDARY_CLIENT_SECRET", ""),
            namespace=getattr(settings, "EVENT_MESH_SECONDARY_NAMESPACE", "default"),
            timeout=float(getattr(settings, "EVENT_MESH_TIMEOUT_S", 30.0)),
            # Left at None until now — ``EventMeshConsumer`` waits on the
            # bridged coroutine with this value, so None meant "wait forever".
            handler_timeout_s=float(
                getattr(settings, "BROKER_HANDLER_TIMEOUT_S", 600.0)
            ),
            max_retries=int(getattr(settings, "EVENT_MESH_MAX_RETRIES", 3)),
            max_payload_bytes=int(
                getattr(settings, "EVENT_MESH_MAX_PAYLOAD_BYTES", 900_000)
            ),
            buffer_size=int(getattr(settings, "EVENT_MESH_BUFFER_SIZE", 10_000)),
            buffer_policy=str(getattr(settings, "EVENT_MESH_BUFFER_POLICY", "drop")),
            flush_batch_size=int(
                getattr(settings, "EVENT_MESH_FLUSH_BATCH_SIZE", 50)
            ),
            flush_concurrency=int(
                getattr(settings, "EVENT_MESH_FLUSH_CONCURRENCY", 10)
            ),
            max_connections=int(
                getattr(settings, "EVENT_MESH_MAX_CONNECTIONS", 100)
            ),
            max_keepalive_connections=int(
                getattr(settings, "EVENT_MESH_MAX_KEEPALIVE_CONNECTIONS", 20)
            ),
            circuit_failure_threshold=int(
                getattr(settings, "EVENT_MESH_CIRCUIT_THRESHOLD", 5)
            ),
            circuit_recovery_timeout_s=float(
                getattr(settings, "EVENT_MESH_CIRCUIT_RECOVERY_S", 30.0)
            ),
            enable_dlq=bool(
                str(getattr(settings, "EVENT_MESH_DLQ_ENABLED", "1")).lower()
                not in ("0", "false", "no")
            ),
            lock_duration_ms=int(
                getattr(settings, "EVENT_MESH_LOCK_DURATION_MS", 60_000)
            ),
            # Same redelivery policy as the primary: a cap without a DMQ
            # discards, and no cap at all re-loops forever.
            dmq_enabled=str(
                getattr(settings, "EVENT_MESH_DMQ_ENABLED", "1")
            ).lower() not in ("0", "false", "no", ""),
            dmq_suffix=str(getattr(settings, "EVENT_MESH_DMQ_SUFFIX", "-dmq")),
            max_redelivery_count=int(
                getattr(settings, "EVENT_MESH_MAX_REDELIVERY_COUNT", 5)
            ),
            poll_interval_s=float(
                getattr(settings, "EVENT_MESH_POLL_INTERVAL_S", 0.5)
            ),
            # Secondary broker stays on REST consume — its amqp10ws endpoint
            # isn't resolved here (separate tenant; lower 429 pressure). Flip
            # by parsing EVENT_MESH_SECONDARY_AMQP_URL if it ever needs push.
            consume_transport="rest",
        )
        client = EventMeshBrokerClient(config, monitor=monitor)
        _logging.getLogger(__name__).info(
            "Secondary Event Mesh broker constructed (binding=%s)",
            getattr(settings, "EVENT_MESH_SECONDARY_BINDING_NAME", ""),
        )
        return client
    except Exception:
        _logging.getLogger(__name__).exception(
            "Failed to create secondary Event Mesh broker — secondary disabled"
        )
        return None


@dataclass(frozen=True)
class InfrastructureSet:
    logger: ILogger
    monitor: IMonitor
    repos: RepositorySet
    messaging: MessagingSet
    db: Optional[object]
    broker: Optional[object]
    # Optional secondary broker — present only when
    # ``EVENT_MESH_SECONDARY_BINDING_NAME`` is configured and the binding
    # exists in VCAP_SERVICES. Used by ``BrokerRegistry`` to route
    # specific purposes (e.g. per-instance response topics) to a fresh
    # tenant when the primary broker's quota is exhausted.
    broker_secondary: Optional[object] = None

    async def start_async(self) -> None:
        """Start async background tasks on broker client (Event Mesh flusher, etc.)."""
        for broker in (self.broker, self.broker_secondary):
            if broker is not None and hasattr(broker, "start_async"):
                try:
                    await broker.start_async()
                except Exception:
                    _logging.getLogger(__name__).exception(
                        "Broker start_async failed — publishes will raise until "
                        "the async flusher starts (no synchronous publish path)"
                    )

    async def stop_async(self, drain_timeout_s: float = 10.0) -> None:
        """Gracefully drain broker send buffers before shutdown."""
        for broker in (self.broker, self.broker_secondary):
            if broker is not None and hasattr(broker, "stop_async"):
                try:
                    await broker.stop_async(drain_timeout_s=drain_timeout_s)
                except Exception:
                    _logging.getLogger(__name__).exception(
                        "Broker stop_async failed during shutdown"
                    )

    def close(self) -> None:
        for broker in (self.broker, self.broker_secondary):
            if broker is not None and hasattr(broker, "close"):
                broker.close()
        if self.db is not None and hasattr(self.db, "close"):
            self.db.close()


def create_infrastructure(settings: Any) -> InfrastructureSet:
    db = _create_database_connection(settings)

    log = create_logger(settings)
    monitor = create_monitor(settings)
    # Broker client needs monitor for metrics (Event Mesh reliability telemetry)
    broker = _create_message_broker_client(settings, monitor=monitor)
    broker_secondary = _create_secondary_broker_client(settings, monitor=monitor)
    repos = create_repositories(settings, db)
    messaging = create_messaging(settings, broker)

    return InfrastructureSet(
        logger=log,
        monitor=monitor,
        repos=repos,
        messaging=messaging,
        db=db,
        broker=broker,
        broker_secondary=broker_secondary,
    )
