import os
import logging
import tempfile
from pydantic_settings import BaseSettings

from .vcap_util import (
    get_hana_credentials,
    get_event_mesh_credentials,
    get_event_mesh_secondary_credentials,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Credential loading. HANA: VCAP_SERVICES (priority) with env-var fallback.
# Event Mesh: environment variables (priority) with VCAP_SERVICES fallback.
# ══════════════════════════════════════════════════════════════════════════════

# HANA credentials - VCAP_SERVICES takes priority
_hana_creds = get_hana_credentials()
if _hana_creds:
    logger.info("Using HANA credentials from VCAP_SERVICES")
    _HANA_HOST = _hana_creds.host
    _HANA_PORT = _hana_creds.port
    _HANA_USERNAME = _hana_creds.user
    _HANA_PASSWORD = _hana_creds.password
    _HANA_SCHEMA = _hana_creds.schema
    _HANA_ENCRYPT = str(_hana_creds.encrypt).lower()
    _HANA_SSL_CERT = _hana_creds.certificate
    _HANA_HDI_USERNAME = _hana_creds.hdi_user
    _HANA_HDI_PASSWORD = _hana_creds.hdi_password
    logger.info(
        "VCAP HANA: host=%s, user=%s, hdi_user=%s, schema=%s",
        _HANA_HOST, _HANA_USERNAME, _HANA_HDI_USERNAME, _HANA_SCHEMA,
    )
else:
    logger.info("Using HANA credentials from environment variables")
    _HANA_HOST = os.getenv("HANA_HOST", "")
    _HANA_PORT = int(os.getenv("HANA_PORT", "443"))
    _HANA_USERNAME = os.getenv("HANA_USERNAME", "")
    _HANA_PASSWORD = os.getenv("HANA_PASSWORD", "")
    _HANA_SCHEMA = os.getenv("HANA_SCHEMA", "")
    _HANA_ENCRYPT = os.getenv("HANA_ENCRYPT", "true")
    _HANA_SSL_CERT = os.getenv("HANA_SSL_CERT", "")
    _HANA_HDI_USERNAME = os.getenv("HANA_HDI_USERNAME", "")
    _HANA_HDI_PASSWORD = os.getenv("HANA_HDI_PASSWORD", "")

# Event Mesh credentials — environment variables take priority over
# VCAP_SERVICES. A complete EVENT_MESH_* set in the environment means the
# operator explicitly pointed this instance at an Event Mesh tenant: it
# overrides any bound VCAP instance and promotes Event Mesh to primary
# broker (see _create_message_broker_client in layer4_frameworks).
_EM_FIELDS = ("token_url", "messaging_url", "management_url", "client_id", "client_secret", "namespace", "amqp_url")
_EM_REQUIRED = ("token_url", "messaging_url", "client_id", "client_secret")


def _resolve_event_mesh_config(env=None) -> tuple[dict, str]:
    """Resolve Event Mesh credentials and their source.

    Returns ``(config, source)`` where ``config`` maps the lowercase field
    names in ``_EM_FIELDS`` to values and ``source`` is:
      - ``"env"``  — the complete required set is present in the environment
      - ``"vcap"`` — taken from a VCAP_SERVICES binding
      - ``""``     — neither (partial env values are still returned as fallback)
    """
    env = os.environ if env is None else env
    env_values = {name: env.get(f"EVENT_MESH_{name.upper()}", "") for name in _EM_FIELDS}
    creds = get_event_mesh_credentials()
    if all(env_values[name] for name in _EM_REQUIRED):
        # Core credentials come from env (env wins), but auto-discover the
        # optional fields from a bound VCAP service when they weren't set
        # explicitly — so AMQP (amqp10ws endpoint) and the instance namespace
        # come from the service binding even when creds are supplied via env.
        # This lets `EVENT_MESH_CONSUME_TRANSPORT=amqp` (the default) work by
        # simply binding the Event Mesh service, with no EVENT_MESH_AMQP_URL.
        if creds:
            for opt in ("amqp_url", "namespace"):
                if not env_values.get(opt):
                    env_values[opt] = getattr(creds, opt, "")
        return env_values, "env"
    if creds:
        return {
            "token_url": creds.token_url,
            "messaging_url": creds.messaging_url,
            "management_url": creds.management_url,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "namespace": creds.namespace,
            "amqp_url": getattr(creds, "amqp_url", ""),
        }, "vcap"
    return env_values, ""


_em_cfg, _EVENT_MESH_CONFIG_SOURCE = _resolve_event_mesh_config()
if _EVENT_MESH_CONFIG_SOURCE == "env":
    logger.info("Using Event Mesh credentials from environment variables (overrides VCAP_SERVICES)")
elif _EVENT_MESH_CONFIG_SOURCE == "vcap":
    logger.info("Using Event Mesh credentials from VCAP_SERVICES")
else:
    logger.info("Event Mesh credentials not fully configured (no complete env set, no VCAP binding)")
_EVENT_MESH_TOKEN_URL = _em_cfg["token_url"]
_EVENT_MESH_MESSAGING_URL = _em_cfg["messaging_url"]
_EVENT_MESH_MANAGEMENT_URL = _em_cfg["management_url"]
_EVENT_MESH_CLIENT_ID = _em_cfg["client_id"]
_EVENT_MESH_CLIENT_SECRET = _em_cfg["client_secret"]
_EVENT_MESH_NAMESPACE = _em_cfg["namespace"]
_EVENT_MESH_AMQP_URL = _em_cfg.get("amqp_url", "")

# Secondary Event Mesh — optional. Used to relieve quota pressure on the
# primary broker by routing specific high-cardinality resources (e.g.
# per-instance response topics) to a second Event Mesh service binding.
# When EVENT_MESH_SECONDARY_BINDING_NAME is set, look up that named
# binding in VCAP_SERVICES. Otherwise fall back to plain env vars (rare;
# typical SAP BTP deployments use VCAP).
_EVENT_MESH_SECONDARY_BINDING_NAME = os.getenv("EVENT_MESH_SECONDARY_BINDING_NAME", "")
_em_secondary_creds = (
    get_event_mesh_secondary_credentials(_EVENT_MESH_SECONDARY_BINDING_NAME)
    if _EVENT_MESH_SECONDARY_BINDING_NAME
    else None
)
if _em_secondary_creds:
    logger.info(
        "Using SECONDARY Event Mesh credentials from VCAP_SERVICES (binding=%s)",
        _EVENT_MESH_SECONDARY_BINDING_NAME,
    )
    _EVENT_MESH_SECONDARY_TOKEN_URL = _em_secondary_creds.token_url
    _EVENT_MESH_SECONDARY_MESSAGING_URL = _em_secondary_creds.messaging_url
    _EVENT_MESH_SECONDARY_MANAGEMENT_URL = _em_secondary_creds.management_url
    _EVENT_MESH_SECONDARY_CLIENT_ID = _em_secondary_creds.client_id
    _EVENT_MESH_SECONDARY_CLIENT_SECRET = _em_secondary_creds.client_secret
    _EVENT_MESH_SECONDARY_NAMESPACE = _em_secondary_creds.namespace
else:
    _EVENT_MESH_SECONDARY_TOKEN_URL = os.getenv("EVENT_MESH_SECONDARY_TOKEN_URL", "")
    _EVENT_MESH_SECONDARY_MESSAGING_URL = os.getenv("EVENT_MESH_SECONDARY_MESSAGING_URL", "")
    _EVENT_MESH_SECONDARY_MANAGEMENT_URL = os.getenv("EVENT_MESH_SECONDARY_MANAGEMENT_URL", "")
    _EVENT_MESH_SECONDARY_CLIENT_ID = os.getenv("EVENT_MESH_SECONDARY_CLIENT_ID", "")
    _EVENT_MESH_SECONDARY_CLIENT_SECRET = os.getenv("EVENT_MESH_SECONDARY_CLIENT_SECRET", "")
    _EVENT_MESH_SECONDARY_NAMESPACE = os.getenv("EVENT_MESH_SECONDARY_NAMESPACE", "")


class Settings(BaseSettings):
    """Workflow Control Plane configuration.

    Credentials can be provided via:
    1. VCAP_SERVICES (recommended for SAP BTP Cloud Foundry deployments)
    2. Environment variables (for local development or non-CF deployments)

    HANA: VCAP_SERVICES takes priority when available. Event Mesh: a complete
    EVENT_MESH_* environment set takes priority over VCAP_SERVICES and also
    selects Event Mesh as the primary message broker.
    """

    # Base fields (internalized from shared.layer0_core.base_settings)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    S3_ENDPOINT: str = os.getenv("S3_ENDPOINT", "http://localhost:9000")
    S3_BUCKET: str = os.getenv("S3_BUCKET", "ai-workflow")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    APP_NAME: str = "Workflow Control Plane"
    APP_MODE: str = os.getenv("APP_MODE", "REST")

    # Infrastructure mode toggle: "mock" | "sqlite" | "local" | "sap"
    INFRA_MODE: str = os.getenv("INFRA_MODE", "mock")

    # SAP HANA configuration (from VCAP_SERVICES or environment variables)
    HANA_HOST: str = _HANA_HOST
    HANA_PORT: int = _HANA_PORT if isinstance(_HANA_PORT, int) else int(_HANA_PORT)
    HANA_USERNAME: str = _HANA_USERNAME
    HANA_PASSWORD: str = _HANA_PASSWORD
    HANA_SCHEMA: str = _HANA_SCHEMA
    HANA_ENCRYPT: str = _HANA_ENCRYPT
    HANA_SSL_CERT: str = _HANA_SSL_CERT
    HANA_HDI_USERNAME: str = _HANA_HDI_USERNAME
    HANA_HDI_PASSWORD: str = _HANA_HDI_PASSWORD
    HANA_POOL_SIZE: int = int(os.getenv("HANA_POOL_SIZE", "5"))
    HANA_MAX_OVERFLOW: int = int(os.getenv("HANA_MAX_OVERFLOW", "10"))
    HANA_POOL_RECYCLE: int = int(os.getenv("HANA_POOL_RECYCLE", "3600"))

    # Schema-bootstrap toggle.
    #   true  (default) → app self-migrates its tables at startup via SQL DDL
    #                     (preferring the DT/hdi account). Use without an HDI deployer.
    #   false           → an external @sap/hdi-deploy task owns the schema; the app
    #                     issues no DDL. Set false where the schema is HDI-managed
    #                     (a managed hdi-shared container forbids runtime CREATE).
    HANA_RUN_MIGRATIONS: bool = os.getenv("HANA_RUN_MIGRATIONS", "true").lower() == "true"

    # Async HANA execution (Part 2 of the async-I/O fix). When enabled,
    # ``HanaConnectionManager.aexecute_*`` offloads sync ``hdbcli`` calls
    # to a dedicated ``ThreadPoolExecutor`` so the FastAPI event loop
    # stays unblocked during DB roundtrips. Sized to match
    # ``HANA_POOL_SIZE + HANA_MAX_OVERFLOW`` so a connection is never
    # starved waiting for a thread. Sync ``execute_*`` paths still work
    # for migrations / startup. Flip on after the per-pod connection
    # pool is sized against HANA's ``max_concurrent_sessions``.
    ASYNC_HANA_ENABLED: bool = os.getenv(
        "ASYNC_HANA_ENABLED", "false",
    ).lower() in ("1", "true", "yes")
    HANA_EXECUTOR_THREADS: int = int(
        os.getenv("HANA_EXECUTOR_THREADS", "15"),
    )

    # SA-1905 claim-check — streaming large node output to CSV.
    #   WORKER_CHUNKED_OUTPUT_ENABLED gates the streaming JSONL->CSV producer
    #   (native ``json_csv_streamer``). When ON *and* a ``store_data`` payload
    #   is estimated to exceed CHUNKING_THRESHOLD_BYTES, the payload is streamed
    #   to CSV via the native extension (bounded RAM) instead of the in-RAM
    #   ``dict_to_csv`` (measured 5.5-9.3x blow-up + a 128 MB/cell ceiling).
    #   OFF, below threshold, or when the native wheel isn't installed → the
    #   existing in-RAM path, so behaviour is byte-for-byte unchanged.
    WORKER_CHUNKED_OUTPUT_ENABLED: bool = os.getenv(
        "WORKER_CHUNKED_OUTPUT_ENABLED", "false",
    ).lower() in ("1", "true", "yes")
    CHUNKING_THRESHOLD_BYTES: int = int(
        os.getenv("CHUNKING_THRESHOLD_BYTES", str(1024 * 1024)),  # 1 MB default
    )
    # SA-1905 SA-1910 — recursively split NESTED array-of-dicts (at any depth)
    # into their own child CSV artifacts (stamped __parent_row_id) instead of
    # JSON cells, so nested tabular data is OData-queryable and never hits the
    # 128 MB cell limit. Round-trip-lossless either way; OFF keeps the legacy
    # depth-1 split (nested arrays stay JSON cells), so it's safe to deploy off.
    RECURSIVE_ARTIFACT_SPLIT: bool = os.getenv(
        "RECURSIVE_ARTIFACT_SPLIT", "false",
    ).lower() in ("1", "true", "yes")

    # SA-1905 — the nested Arrow sidecar: one file holding the WHOLE recursive
    # value, so reading it needs no download tree, no re-parse and no
    # __parent_row_id join. The CSV is still written, byte for byte, and is
    # still what the steward edits and OData pages.
    #
    #   ARROW_READ_MODE   off    — never look at arrow_file_id (default)
    #                     shadow — read BOTH, log any disagreement, return CSV
    #                     on     — return Arrow, falling back to CSV on failure
    #   ARROW_SIDECAR_WRITE_ENABLED — write the sidecar alongside the CSV
    #   ARROW_STRICT_PARITY — make the Arrow reader reproduce the CSV reading of
    #                     the shapes where the two legitimately differ. ON by
    #                     default, so enabling the feature is a provable no-op:
    #                     "reads got fast" does not ship in the same breath as
    #                     "this value means something new". Turning it OFF is a
    #                     separate decision, with the divergence list in hand.
    ARROW_READ_MODE: str = os.getenv("ARROW_READ_MODE", "off").lower()
    ARROW_SIDECAR_WRITE_ENABLED: bool = os.getenv(
        "ARROW_SIDECAR_WRITE_ENABLED", "false",
    ).lower() in ("1", "true", "yes")
    ARROW_STRICT_PARITY: bool = os.getenv(
        "ARROW_STRICT_PARITY", "true",
    ).lower() in ("1", "true", "yes")
    # The sidecar's OWN size floor. Defaults to CHUNKING_THRESHOLD_BYTES only
    # for back-compat; that borrowed value (1 MB) defines an empty window,
    # because an output above the broker cap never reaches the control plane.
    # 0 = write one for every file-backed value.
    ARROW_SIDECAR_MIN_BYTES: int = int(
        os.getenv("ARROW_SIDECAR_MIN_BYTES", str(CHUNKING_THRESHOLD_BYTES)),
    )
    # Arrow IPC compression for the sidecar: none | lz4 | zstd.
    #
    # Default "none" so the wheel that CAN compress ships INERT. The feature is
    # symmetric — the same ``ipc_compression`` build flag grants reading and
    # writing — and the control plane is the ONLY reader, so the order is:
    # rebuild every image, verify arrow_read_total{outcome="fallback"} is still
    # zero, THEN set this. Flipped early, a reader on the old wheel cannot
    # decode, falls through to CSV silently, and under ARROW_READ_MODE=on that
    # restores the old ""-reads-back-as-null semantics.
    #
    # Measured on a real 481 042 B sidecar: lz4 3.4x, zstd 4.3x, at +2-3 ms to
    # write and +3 ms to read. See solace/specs/wcp-arrow-size-reduction-plan.md.
    ARROW_SIDECAR_COMPRESSION: str = os.getenv("ARROW_SIDECAR_COMPRESSION", "none").lower()
    # SA-1910 — build the nested-array tree with the native crate (shared with
    # worker-sdk) instead of the in-RAM Python writer. Falls back to Python on
    # any failure, so the risk of turning it on is orphaned files, not lost
    # output.
    NATIVE_RECURSIVE_SPLIT: bool = os.getenv(
        "NATIVE_RECURSIVE_SPLIT", "false",
    ).lower() in ("1", "true", "yes")

    # SA-1942 — CP variable-resolver OOM guard. The resolver's whole-value
    # (``{{$node.output}}``) and deep-path materialize routes
    # (``file_backed_resolver``) download a file_ref's FULL content into RAM
    # (``download_file`` → all bytes → ``csv_to_dict``). A large output
    # (100 MB–1 GB) OOMs the ~1 GB control-plane pod (which budgets only
    # ~80 MB/run for the run-scoped materialize cache — see
    # ORCHESTRATION_WORKER_COUNT). Above this cap the resolver, instead of
    # full-downloading, returns a lazy metadata-backed handle for a flat
    # row-range-eligible ref (len/iteration by row-range, no whole download)
    # or raises a clear VariableResolutionError for a non-flat ref that can't
    # be row-ranged. Size is probed cheaply via the file service's HEAD
    # (``head_file``), never by downloading. ``0`` disables the guard
    # (legacy unbounded full-materialize). Default 64 MB — comfortably above
    # any legitimate inline compute/condition value, so only pathologically
    # large values change behaviour.
    RESOLVE_MATERIALIZE_MAX_BYTES: int = int(
        os.getenv("RESOLVE_MATERIALIZE_MAX_BYTES", str(64 * 1024 * 1024)),
    )

    # Messaging infrastructure override
    MESSAGING_MODE: str = os.getenv("MESSAGING_MODE", "")

    # Kafka configuration
    KAFKA_GROUP_ID: str = os.getenv("KAFKA_GROUP_ID", "ai-workflow")
    KAFKA_AUTO_OFFSET_RESET: str = os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest")

    # SAP Event Mesh configuration (from VCAP_SERVICES or environment variables)
    EVENT_MESH_TOKEN_URL: str = _EVENT_MESH_TOKEN_URL
    EVENT_MESH_MESSAGING_URL: str = _EVENT_MESH_MESSAGING_URL
    EVENT_MESH_MANAGEMENT_URL: str = _EVENT_MESH_MANAGEMENT_URL
    EVENT_MESH_CLIENT_ID: str = _EVENT_MESH_CLIENT_ID
    EVENT_MESH_CLIENT_SECRET: str = _EVENT_MESH_CLIENT_SECRET
    EVENT_MESH_NAMESPACE: str = _EVENT_MESH_NAMESPACE
    # Where the Event Mesh credentials came from: "env" | "vcap" | "".
    # "env" promotes Event Mesh to primary broker regardless of INFRA_MODE
    # (an explicit MESSAGING_MODE still wins).
    EVENT_MESH_CONFIG_SOURCE: str = _EVENT_MESH_CONFIG_SOURCE

    # Optional SECONDARY Event Mesh — relieves primary-broker quota by
    # routing specific resources (per-instance response topics today;
    # add more via BrokerRegistry purposes). When the binding name is
    # set + the binding is bound in VCAP_SERVICES, the secondary broker
    # is constructed; otherwise these stay empty and the registry falls
    # back to the primary broker.
    EVENT_MESH_SECONDARY_BINDING_NAME: str = _EVENT_MESH_SECONDARY_BINDING_NAME
    EVENT_MESH_SECONDARY_TOKEN_URL: str = _EVENT_MESH_SECONDARY_TOKEN_URL
    EVENT_MESH_SECONDARY_MESSAGING_URL: str = _EVENT_MESH_SECONDARY_MESSAGING_URL
    EVENT_MESH_SECONDARY_MANAGEMENT_URL: str = _EVENT_MESH_SECONDARY_MANAGEMENT_URL
    EVENT_MESH_SECONDARY_CLIENT_ID: str = _EVENT_MESH_SECONDARY_CLIENT_ID
    EVENT_MESH_SECONDARY_CLIENT_SECRET: str = _EVENT_MESH_SECONDARY_CLIENT_SECRET
    EVENT_MESH_SECONDARY_NAMESPACE: str = _EVENT_MESH_SECONDARY_NAMESPACE

    # SAP Event Mesh reliability tuning (Phase 1)
    EVENT_MESH_TIMEOUT_S: float = float(os.getenv("EVENT_MESH_TIMEOUT_S", "30.0"))
    # Idle poll cadence of the queue consumer loop. Raise this (e.g. 2.0) to
    # cut the baseline REST call rate on plans with tight 429 rate limits.
    EVENT_MESH_POLL_INTERVAL_S: float = float(os.getenv("EVENT_MESH_POLL_INTERVAL_S", "0.5"))
    # Messages drained per queue per REST poll pass. 1 restores the old
    # one-message-per-pass behaviour, which spread a burst of sibling
    # orchestration actions across successive poll intervals (measured
    # 2026-08-05: 13.5s and 15.8s between siblings of one run).
    EVENT_MESH_CONSUME_BATCH_MAX: int = int(
        os.getenv("EVENT_MESH_CONSUME_BATCH_MAX", "10"),
    )
    # Dead-message queue + redelivery cap. SAP's guidance is explicit: leaving
    # maxRedeliveryCount at 0/empty means INDEFINITE, IMMEDIATE redelivery.
    # Under AMQP push a failed handler settles the delivery as RELEASED, so a
    # poison message re-loops at full speed forever. The cap needs a DMQ or the
    # message is discarded once the cap trips — hence the two ship together.
    # EVENT_MESH_DMQ_ENABLED=0 restores the legacy unbounded behaviour.
    EVENT_MESH_DMQ_ENABLED: str = os.getenv("EVENT_MESH_DMQ_ENABLED", "1")
    EVENT_MESH_DMQ_SUFFIX: str = os.getenv("EVENT_MESH_DMQ_SUFFIX", "-dmq")
    EVENT_MESH_MAX_REDELIVERY_COUNT: int = int(
        os.getenv("EVENT_MESH_MAX_REDELIVERY_COUNT", "5"),
    )
    EVENT_MESH_MAX_RETRIES: int = int(os.getenv("EVENT_MESH_MAX_RETRIES", "3"))
    EVENT_MESH_MAX_PAYLOAD_BYTES: int = int(os.getenv("EVENT_MESH_MAX_PAYLOAD_BYTES", "900000"))

    # SAP Event Mesh async flusher (Phase 2)
    EVENT_MESH_BUFFER_SIZE: int = int(os.getenv("EVENT_MESH_BUFFER_SIZE", "10000"))
    EVENT_MESH_BUFFER_POLICY: str = os.getenv("EVENT_MESH_BUFFER_POLICY", "drop")
    EVENT_MESH_FLUSH_BATCH_SIZE: int = int(os.getenv("EVENT_MESH_FLUSH_BATCH_SIZE", "50"))
    EVENT_MESH_FLUSH_CONCURRENCY: int = int(os.getenv("EVENT_MESH_FLUSH_CONCURRENCY", "10"))
    EVENT_MESH_MAX_CONNECTIONS: int = int(os.getenv("EVENT_MESH_MAX_CONNECTIONS", "100"))
    EVENT_MESH_MAX_KEEPALIVE_CONNECTIONS: int = int(os.getenv("EVENT_MESH_MAX_KEEPALIVE_CONNECTIONS", "20"))

    # SAP Event Mesh circuit breaker + DLQ (Phase 3)
    EVENT_MESH_CIRCUIT_THRESHOLD: int = int(os.getenv("EVENT_MESH_CIRCUIT_THRESHOLD", "5"))
    EVENT_MESH_CIRCUIT_RECOVERY_S: float = float(os.getenv("EVENT_MESH_CIRCUIT_RECOVERY_S", "30.0"))
    EVENT_MESH_DLQ_ENABLED: str = os.getenv("EVENT_MESH_DLQ_ENABLED", "1")

    # DLQ durability (SA-1471). Local JSONL spool that parks durable envelopes
    # whenever the DLQ path can't take them (disabled / no client / broker
    # down); replayed automatically when the circuit closes and at startup.
    # Default ON (tempdir survives a broker outage; a container restart still
    # loses it — the spool narrows the loss window, it is not a WAL). Set ""
    # to disable. BEST_EFFORT_TOPICS: comma-separated topics that MAY drop.
    # Alert rules: docs/runbooks/event-mesh-dlq.md.
    EVENT_MESH_DLQ_SPOOL_DIR: str = os.getenv(
        "EVENT_MESH_DLQ_SPOOL_DIR",
        os.path.join(tempfile.gettempdir(), "eventmesh-dlq-spool"),
    )
    EVENT_MESH_DLQ_BEST_EFFORT_TOPICS: str = os.getenv(
        "EVENT_MESH_DLQ_BEST_EFFORT_TOPICS", "realtime.events",
    )

    # Namespace hardening (SA-1468). REQUIRE: an unset/empty namespace becomes
    # a boot-time failure (EventMeshConfigError — the pod refuses to start)
    # instead of runtime 403s + self-heal. Default false preserves historical
    # behaviour; flip on in prod manifests. SELF_HEAL: keeps the 403-adoption
    # path as a dev convenience; set 0 to surface 403s without mutating the
    # shared namespace at runtime.
    EVENT_MESH_REQUIRE_NAMESPACE: bool = os.getenv("EVENT_MESH_REQUIRE_NAMESPACE", "false").lower() in ("1", "true", "yes")
    EVENT_MESH_ALLOW_NAMESPACE_SELF_HEAL: bool = os.getenv("EVENT_MESH_ALLOW_NAMESPACE_SELF_HEAL", "true").lower() not in ("0", "false", "no")

    # Message lock TTL — how long Event Mesh holds a consumed message before
    # redelivering if the consumer hasn't acked. Short lock + slow handler =
    # redelivery = out-of-order events at the frontend (visible as "late
    # event run_seq=N (next=M) — emitting out of order" in socketio_consumer).
    # 60 s matches the broker default; bump higher (e.g. 300000) if handler
    # tail latency exceeds 60 s under load.
    EVENT_MESH_LOCK_DURATION_MS: int = int(os.getenv("EVENT_MESH_LOCK_DURATION_MS", "60000"))

    # SAP Event Mesh consume transport. "amqp" uses a push-based AMQP 1.0
    # (over WebSocket) consumer that eliminates the per-instance 429s caused by
    # polling the REST consumption endpoint; "rest" keeps the legacy poll loop.
    # AMQP auto-falls-back to REST when the AMQP libs are missing or the endpoint
    # can't be reached, so "amqp" is safe to default. The AMQP URL is taken
    # from the service key's amqp10ws binding (EVENT_MESH_AMQP_URL overrides).
    EVENT_MESH_CONSUME_TRANSPORT: str = os.getenv("EVENT_MESH_CONSUME_TRANSPORT", "amqp")

    # 429 isolation + REST-fallback observability (SA-1470).
    # COOLDOWN_SCOPE: "instance" (default — SAP EM's REST limit is per service
    # instance; one 429 pauses every queue, now alertable + time-bounded) or
    # "queue" (only the throttled queue pauses). REQUIRE_AMQP: when true and
    # the consumer is forced onto the REST fallback, /api/v1/health/readiness
    # reports 503 so the platform pulls the pod instead of running silently
    # degraded. Default false — same rollout pattern as SA-1466's gate.
    EVENT_MESH_429_COOLDOWN_SCOPE: str = os.getenv("EVENT_MESH_429_COOLDOWN_SCOPE", "instance")
    EVENT_MESH_REQUIRE_AMQP: bool = os.getenv("EVENT_MESH_REQUIRE_AMQP", "false").lower() in ("1", "true", "yes")

    EVENT_MESH_AMQP_URL: str = _EVENT_MESH_AMQP_URL or os.getenv("EVENT_MESH_AMQP_URL", "")
    # WebSocket upgrade path — the amqp10ws endpoint already carries it; override
    # only if the service key's path differs from the default.
    EVENT_MESH_AMQP_WS_PATH: str = os.getenv("EVENT_MESH_AMQP_WS_PATH", "/protocols/amqp10ws")
    # In-flight messages per receiver link. Concurrency is 1 (ordering), so
    # this is the depth behind a serial handler, and the broker's redelivery
    # clock starts when it hands a message over — not when the handler gets to
    # it. 10 against the 60 s queue lock TTL tolerates ~6 s per message; 50
    # tolerated only ~1.2 s.
    EVENT_MESH_AMQP_PREFETCH: int = int(os.getenv("EVENT_MESH_AMQP_PREFETCH", "10"))
    # Control-plane consumers are order-sensitive — keep one receiver link/queue.
    EVENT_MESH_AMQP_CONCURRENCY: int = int(os.getenv("EVENT_MESH_AMQP_CONCURRENCY", "1"))
    EVENT_MESH_AMQP_STRIP_NAMESPACE: str = os.getenv("EVENT_MESH_AMQP_STRIP_NAMESPACE", "0")
    # Emergency only. A failed AMQP attach used to switch the process to the
    # REST poll loop on the RETIRED bare queue, which re-creates the
    # subscription P5.4 deleted and fans the topic out to two queues.
    # Reproduced on tenant-1 2026-08-11: five topics, every message duplicated,
    # 3 stale consumer flows per queue, from a 65-second two-instance window.
    # Default off — a failed attach now stands the topic down and retries.
    # Turn on only if standing down is worse than duplicating, and read
    # tests/unit/test_event_mesh_scale_out_resubscribe.py first.
    # Cold connection 19.4ms vs warm request 6.0ms. Grouping is the first
    # thing that asks the pool for more than one connection, so an idle gap
    # longer than this now costs several handshakes rather than one.
    EVENT_MESH_KEEPALIVE_EXPIRY_S: float = float(
        os.getenv("EVENT_MESH_KEEPALIVE_EXPIRY_S", "30.0")
    )
    EVENT_MESH_RUNTIME_FALLBACK: bool = os.getenv(
        "EVENT_MESH_RUNTIME_FALLBACK", "false",
    ).lower() in ("1", "true", "yes")

    # Service-specific
    SERVICE_NAME: str = "workflow-control-plane"
    SERVICE_PORT: int = int(os.getenv("SERVICE_PORT", "8001"))
    KAFKA_TASK_TOPIC: str = os.getenv("KAFKA_TASK_TOPIC", "workflow.tasks")
    KAFKA_RESULT_TOPIC: str = os.getenv("KAFKA_RESULT_TOPIC", "workflow.results")
    WORKER_EXECUTION_URL: str = os.getenv("WORKER_EXECUTION_URL", "http://localhost:8004")
    AGENT_SERVICE_URL: str = os.getenv("AGENT_SERVICE_URL", "")
    FILE_SERVICE_URL: str = os.getenv("FILE_SERVICE_URL", "")
    DATA_FACTORY_URL: str = os.getenv("DATA_FACTORY_URL", "")
    SCHEMA_TRANSFORM_URL: str = os.getenv("SCHEMA_TRANSFORM_URL", "")
    GOVERNANCE_API_URL: str = os.getenv("GOVERNANCE_API_URL", "")
    INTEGRATION_HUB_API_URL: str = os.getenv("INTEGRATION_HUB_API_URL", "")
    # Credentials vault (credentials-management) — run-time credential_ref resolution.
    # Setting CREDENTIALS_VAULT_URL activates the StartRun producer; empty = dormant.
    CREDENTIALS_VAULT_URL: str = os.getenv("CREDENTIALS_VAULT_URL", "")
    CREDENTIALS_VAULT_SERVICE_TOKEN: str = os.getenv("CREDENTIALS_VAULT_SERVICE_TOKEN", "")
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

    # ── Keycloak SSO / BFF auth (SA-2530) ──────────────────────────────────
    # Turns the control plane into a Backend-For-Frontend that logs the browser
    # in against Keycloak (Azure-federated) and issues an HttpOnly session
    # cookie. The SPA never holds a token — the OIDC tokens live server-side.
    # AUTH_ENABLED defaults OFF, so an unconfigured deployment behaves exactly
    # as before (identity via the legacy X-User header).
    AUTH_ENABLED: bool = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    KEYCLOAK_ISSUER: str = os.getenv("KEYCLOAK_ISSUER", "")  # e.g. https://<host>/realms/tenant-1
    KEYCLOAK_CLIENT_ID: str = os.getenv("KEYCLOAK_CLIENT_ID", "gateway-bff-fm")
    KEYCLOAK_CLIENT_SECRET: str = os.getenv("KEYCLOAK_CLIENT_SECRET", "")
    KEYCLOAK_REDIRECT_URI: str = os.getenv("KEYCLOAK_REDIRECT_URI", "")  # <public>/api/v1/auth/callback
    KEYCLOAK_SCOPE: str = os.getenv("KEYCLOAK_SCOPE", "openid profile email")
    # Where /auth/callback and /auth/logout send the browser back to (the SPA).
    AUTH_POST_LOGIN_REDIRECT: str = os.getenv("AUTH_POST_LOGIN_REDIRECT", "/")
    AUTH_POST_LOGOUT_REDIRECT: str = os.getenv("AUTH_POST_LOGOUT_REDIRECT", "/")
    # Secret that signs+encrypts the opaque session/state cookies (Fernet). Any
    # non-empty string; REQUIRED when AUTH_ENABLED is true.
    AUTH_SESSION_SECRET: str = os.getenv("AUTH_SESSION_SECRET", "")
    AUTH_SESSION_COOKIE: str = os.getenv("AUTH_SESSION_COOKIE", "wf_session")
    AUTH_SESSION_TTL_SECONDS: int = int(os.getenv("AUTH_SESSION_TTL_SECONDS", "3600"))
    # Cookie flags. Use SAMESITE=none (+ SECURE) when the SPA and this BFF sit
    # on different origins so the session cookie rides cross-site requests.
    AUTH_COOKIE_SECURE: bool = os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
    AUTH_COOKIE_SAMESITE: str = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
    AUTH_COOKIE_DOMAIN: str = os.getenv("AUTH_COOKIE_DOMAIN", "")
    # SA-2533 — audience the backend requires on a forwarded Bearer JWT (matches the
    # Keycloak client's audience mapper). Empty string disables the audience check.
    AUTH_JWT_AUDIENCE: str = os.getenv("AUTH_JWT_AUDIENCE", "smdg-ai-api")

    # Kafka event topics
    WORKFLOW_EVENT_TOPIC: str = os.getenv("WORKFLOW_EVENT_TOPIC", "orchestrator.workflow_event")

    # WCP→executor transport: "http" | "grpc" | "event_mesh".
    # "event_mesh" dispatches tasks to the workflow.tasks topic and consumes
    # results from workflow.results (requires a broker, i.e. MESSAGING_MODE
    # event_mesh/sap/local); "http"/"grpc" use a synchronous call.
    TRANSPORT_MODE: str = os.getenv("TRANSPORT_MODE", "http")
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", "50051"))
    GRPC_MAX_WORKERS: int = int(os.getenv("GRPC_MAX_WORKERS", "10"))
    # SA-1526: gRPC server lifecycle decoupled from TRANSPORT_MODE. Default ON
    # in every mode — the Docker image EXPOSEs :50051 and CF's default port
    # health check probes every exposed port, so a mode-gated server crash-loops
    # non-grpc transports; always-on also keeps external gRPC callers working.
    GRPC_SERVER_ENABLED: bool = os.getenv("GRPC_SERVER_ENABLED", "true").lower() in ("1", "true", "yes")
    WORKER_EXECUTION_GRPC_TARGET: str = os.getenv("WORKER_EXECUTION_GRPC_TARGET", "localhost:50052")

    # Number of OrchestrationWorker coroutines per pod. Default 4 is sized
    # for a 1 GB control-plane pod:
    #   baseline ≈ 450 MB (Python + FastAPI + Pydantic + httpx + SQLAlchemy
    #   + Socket.IO + HANA/broker pools + engine/registries), leaving
    #   ~570 MB. With a 30% safety margin (~400 MB usable) and ~80 MB per
    #   concurrent in-flight run (run state + run-scoped materialization
    #   cache), 4 workers fit comfortably. Larger pods can raise this
    #   proportionally — typically (pod_mb - 450) / 80 → workers, capped
    #   at 8 since beyond that the per-run lock contention dominates.
    # Raising it gives cross-run parallelism (different runs execute
    # concurrently on the same event loop) while same-run actions still
    # serialise via the per-run asyncio.Lock in OrchestrationEngine.
    ORCHESTRATION_WORKER_COUNT: int = int(os.getenv("ORCHESTRATION_WORKER_COUNT", "4"))

    # How long a broker poll thread may block waiting for an async handler it
    # bridged onto the event loop. Every such bridge waits so the ack/commit
    # reflects the handler's outcome; without a bound, ONE stalled handler
    # pins the poll thread for the life of the pod and that topic silently
    # stops being consumed — no crash, no log, no metric, every run stuck.
    #
    # This is wedge insurance, not latency control, so it is deliberately far
    # longer than any legitimate handler: a bound that trips during normal
    # work would no-ack healthy messages and redeliver them. On timeout the
    # in-flight coroutine is orphaned (it keeps running) and the message is
    # redelivered, so a handler that is genuinely stuck will leak one
    # coroutine per redelivery — which is why the timeout is logged and
    # metered rather than swallowed.
    BROKER_HANDLER_TIMEOUT_S: float = float(
        os.getenv("BROKER_HANDLER_TIMEOUT_S", "600")
    )

    # Event-loop lag sampling (SA-2257 P0). The control plane's capacity is
    # bounded by loop time, not by ORCHESTRATION_WORKER_COUNT — there is one
    # loop — and nothing measured that until now. Costs one timer wakeup per
    # interval; on by default because a load test without it can only report
    # whether the pod survived, not where it bent.
    EVENT_LOOP_LAG_ENABLED: bool = os.getenv(
        "EVENT_LOOP_LAG_ENABLED", "true",
    ).lower() in ("1", "true", "yes")
    EVENT_LOOP_LAG_INTERVAL_S: float = float(
        os.getenv("EVENT_LOOP_LAG_INTERVAL_S", "0.01")
    )
    EVENT_LOOP_LAG_REPORT_INTERVAL_S: float = float(
        os.getenv("EVENT_LOOP_LAG_REPORT_INTERVAL_S", "10")
    )
    # A stall this long is worth a log line on its own. 250ms is roughly the
    # measured cost of a DATA_EDIT over a 5MB CSV (SA-2278), i.e. a real
    # freeze rather than timer noise.
    EVENT_LOOP_LAG_WARN_THRESHOLD_S: float = float(
        os.getenv("EVENT_LOOP_LAG_WARN_THRESHOLD_S", "0.25")
    )

    # Cache expiry sweep (SA-2368). ``BoundedTTLCache.purge_expired`` was never
    # called by anything, so a TTL bounded staleness while the VALUE stayed
    # resident until the LRU cap evicted it — measured 3.9 days for a decrypted
    # credential against its own 300s TTL. This timer is what bounds residency
    # to TTL + interval. On by default: a cache whose entries hold secrets must
    # not need an operator to remember a flag.
    CACHE_EXPIRY_SWEEP_ENABLED: bool = os.getenv(
        "CACHE_EXPIRY_SWEEP_ENABLED", "true",
    ).lower() in ("1", "true", "yes")
    # 60s against the 300s credential TTL — residency lands at 5-6 minutes,
    # which is the revocation window the code already claims to honour. Cheaper
    # sweeps buy nothing: the bound is TTL-dominated, not interval-dominated.
    CACHE_EXPIRY_SWEEP_INTERVAL_S: float = float(
        os.getenv("CACHE_EXPIRY_SWEEP_INTERVAL_S", "60")
    )

    # malloc_trim janitor. ``resource_collector.malloc_trim`` shipped in #1962
    # with no call site; measured on tenant-1 2026-08-21 the arena held the
    # identical 976.6 MiB for 7 h while ``in_use`` sat at 42 MiB and the pod ran
    # 2 nodes, and the only mechanism that has ever returned memory on that pod
    # is the process dying.
    #
    # Default OFF, unlike CACHE_EXPIRY_SWEEP_ENABLED. That janitor is on by
    # default because holding a decrypted secret is itself the harm; this one's
    # risk runs the other way — the call is synchronous, its cost has never been
    # measured on chunks this large, and on ``instances: 1`` every millisecond
    # is the whole tenant. Prove it on one org with ``cf set-env`` first, then
    # flip the default in its own change.
    MALLOC_TRIM_ENABLED: bool = os.getenv(
        "MALLOC_TRIM_ENABLED", "false",
    ).lower() in ("1", "true", "yes")
    # 300s, not 60s. The retention does not decay — it sat at one value for
    # 7 hours — so there is nothing a faster timer would catch sooner, and each
    # wakeup is event-loop time on a plane that has none to spare.
    MALLOC_TRIM_INTERVAL_S: float = float(
        os.getenv("MALLOC_TRIM_INTERVAL_S", "300")
    )
    # Below this, a trim cannot repay its own loop time. Idle ``in_use`` on
    # tenant-1 is ~42-52 MiB, so 64 MiB is the first threshold that is
    # unambiguously retention rather than the working set.
    MALLOC_TRIM_MIN_FREE_BYTES: int = int(
        os.getenv("MALLOC_TRIM_MIN_FREE_BYTES", str(64 * 1024 * 1024))
    )
    # Over this, the janitor doubles its own interval and warns. The benchmark
    # in ``read_malloc_info`` is 0.3 ms at 2 KiB chunks and 54.6 ms at 32 KiB;
    # this pod's chunks measured 655-1079 KiB, so the real cost is unknown and
    # the back-off is what bounds it instead of a guess.
    MALLOC_TRIM_MAX_DURATION_MS: float = float(
        os.getenv("MALLOC_TRIM_MAX_DURATION_MS", "100")
    )
    # glibc's ``pad``: bytes to KEEP at the top of the heap. 0 releases all it
    # can and lets the next burst fault its pages back in (anonymous minor
    # faults, ~1 us each); a non-zero pad trades resident bytes for avoiding
    # that. 0 by default because the measured retention is 707-934 MiB and the
    # fault cost is three orders of magnitude smaller.
    MALLOC_TRIM_PAD_BYTES: int = int(os.getenv("MALLOC_TRIM_PAD_BYTES", "0"))

    # Push Gateway for frontend notifications
    PUSH_GATEWAY_URL: str = os.getenv("PUSH_GATEWAY_URL", "")
    PUSH_GATEWAY_PROVIDER: str = os.getenv("PUSH_GATEWAY_PROVIDER", "grpc")  # "grpc" or "http"

    # Child workflow timeout sweeper (temporary)
    CHILD_TIMEOUT_SWEEP_INTERVAL_SECONDS: int = int(os.getenv("CHILD_TIMEOUT_SWEEP_INTERVAL_SECONDS", "5"))

    # Orphaned-parent reconciliation, riding the same sweep tick.
    #
    # A parent stuck at a WORKFLOW node whose child run is already TERMINAL is
    # unreachable by every existing mechanism: the timeout sweep enumerates
    # ``statuses=[RUNNING]`` children (a finished child is invisible to it) and
    # skips any WORKFLOW node whose ``timeout_ms`` is unset, which is the default.
    # One such parent sat RUNNING for 20 minutes on tenant-1 on 2026-08-25 until a
    # human cancelled it.
    #
    # off | shadow | on. Default ``shadow``: it does the scan and logs exactly
    # what it WOULD reconcile, and changes nothing. Read those logs before
    # setting ``on`` — the log line is the evidence, not this default.
    CHILD_ORPHAN_RECONCILE_MODE: str = os.getenv(
        "CHILD_ORPHAN_RECONCILE_MODE", "shadow",
    ).strip().lower()
    # Only act once the child has been terminal this long, so the sweep never
    # races the normal notification path it exists to back up.
    CHILD_ORPHAN_GRACE_SECONDS: int = int(
        os.getenv("CHILD_ORPHAN_GRACE_SECONDS", "60")
    )
    # Bound on RUNNING runs examined per tick. Truncation is LOGGED, never
    # silent — a capped scan that says nothing reads as "nothing to find".
    CHILD_ORPHAN_SCAN_LIMIT: int = int(
        os.getenv("CHILD_ORPHAN_SCAN_LIMIT", "200")
    )
    # The reconciliation rides the 5s timeout tick but does NOT need its
    # cadence — it already waits out a grace window, and each pass costs a
    # RUNNING-runs query plus a run read and a task list per parent. At the 5s
    # tick that is a few queries per second forever, against a control plane
    # that is already tight (SA-2254). Once a minute finds the same orphans.
    CHILD_ORPHAN_SCAN_INTERVAL_SECONDS: int = int(
        os.getenv("CHILD_ORPHAN_SCAN_INTERVAL_SECONDS", "60")
    )

    # Unservable dispatched-task liveness sweep (Signal A, opt-in). When
    # enabled, fails a dispatched TASK whose worker_type has ZERO live workers
    # in the registry (heartbeat liveness) after a short grace. Default OFF —
    # a live worker of the type keeps its tasks safe regardless of duration, so
    # long-running work is never false-failed. See
    # ``sweep_unservable_dispatched_tasks_use_case``.
    WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED: bool = os.getenv("WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED", "false").lower() in ("1", "true", "yes")
    WORKER_DISPATCH_LIVENESS_GRACE_SECONDS: int = int(os.getenv("WORKER_DISPATCH_LIVENESS_GRACE_SECONDS", "60"))
    WORKER_DISPATCH_LIVENESS_SWEEP_INTERVAL_SECONDS: int = int(os.getenv("WORKER_DISPATCH_LIVENESS_SWEEP_INTERVAL_SECONDS", "10"))

    # --- Palette freshness/dedupe (SA-2052, R5) ---
    # A worker registration whose last_heartbeat is older than this window is
    # dropped from BOTH palette paths (v2 WorkerNodeProvider.list_summaries and
    # v1 ListNodePaletteUseCase._fetch_worker_nodes), even if its health_status
    # still reads healthy — the belt-and-suspenders for the window between a
    # worker going silent and R4's executor-side sweep marking it unhealthy.
    # Mirrors the executor's WORKER_STALE_AFTER_SECONDS default (120) so a worker
    # is "stale" at the same age in the palette and the sweep. HONEST LIMIT: these
    # are two independent services' settings and can drift (spec §4.4).
    WORKER_STALE_AFTER_SECONDS: int = int(os.getenv("WORKER_STALE_AFTER_SECONDS", "120"))

    # --- Worker registry observability mirror (SA-2050, R3) ---
    # Opt-in. When enabled on EXACTLY ONE control-plane instance, a background
    # loop snapshot-reconciles the executor's in-memory registry into
    # AIW_WORKER_REGISTRY_MIRROR for DBeaver/SQL observability. Read-only for
    # humans — never a dispatch source. No-op unless HANA is configured
    # (INFRA_MODE in local/sap). Enable on one instance only (delete-on-absent
    # is racy across writers). Default OFF, matching the sibling liveness sweep.
    WORKER_MIRROR_ENABLED: bool = os.getenv("WORKER_MIRROR_ENABLED", "false").lower() in ("1", "true", "yes")
    WORKER_MIRROR_INTERVAL_SECONDS: int = int(os.getenv("WORKER_MIRROR_INTERVAL_SECONDS", "30"))

    # --- Worker replica-health realtime feed (SA-2056, R9) ---
    # Opt-in. When enabled on EXACTLY ONE control-plane instance, a background loop
    # diffs the R6 /workers/replicas aggregate every interval and publishes per-
    # worker_type deltas to the realtime room "workers:health". Default OFF. Two
    # enabled instances -> duplicate deltas, harmless (payload is full per-type
    # state keyed by worker_type, last-write-wins by `at`).
    WORKER_HEALTH_FEED_ENABLED: bool = os.getenv("WORKER_HEALTH_FEED_ENABLED", "false").lower() in ("1", "true", "yes")
    WORKER_HEALTH_FEED_INTERVAL_SECONDS: int = int(os.getenv("WORKER_HEALTH_FEED_INTERVAL_SECONDS", "15"))

    # Schedule (crontab) trigger scheduler. Fires runs for TRIGGER nodes whose
    # config has trigger_type="schedule" + a schedule_cron expr. Firing is NOT
    # idempotent across instances, so enable on EXACTLY ONE control-plane
    # instance (see docs/design/schedule-trigger-scheduler.md). Default off.
    WORKFLOW_SCHEDULER_ENABLED: bool = os.getenv("WORKFLOW_SCHEDULER_ENABLED", "false").lower() in ("1", "true", "yes")
    SCHEDULER_TICK_SECONDS: int = int(os.getenv("SCHEDULER_TICK_SECONDS", "30"))

    # Gateway OpenAPI spec sync poller. Re-fetches every gateway app that has a
    # spec_url and reconciles its functions. Not idempotent across instances —
    # enable on EXACTLY ONE control-plane instance. Default off (on-demand
    # POST /gateway/apps/{name}/sync still works regardless).
    GATEWAY_OPENAPI_SYNC_ENABLED: bool = os.getenv("GATEWAY_OPENAPI_SYNC_ENABLED", "false").lower() in ("1", "true", "yes")
    GATEWAY_OPENAPI_SYNC_INTERVAL_SECONDS: int = int(os.getenv("GATEWAY_OPENAPI_SYNC_INTERVAL_SECONDS", "3600"))

    # SA-1466 — readiness gate for the per-instance response-topic listener.
    # When True, GET /api/v1/health/readiness returns 503 if the listener is
    # degraded (could not subscribe to its per-instance response topic), so a
    # quota-exhausted pod is pulled from rotation in a MULTI-POD deployment
    # instead of silently failing cross-instance EditNodeDataAction responses.
    # Default False preserves the historical behaviour (single-pod fast-path is
    # unaffected). Enable only in multi-pod deployments. See
    # docs/runbooks/response-topic-quota.md.
    RESPONSE_LISTENER_READINESS_GATE_ENABLED: bool = os.getenv("RESPONSE_LISTENER_READINESS_GATE_ENABLED", "false").lower() in ("1", "true", "yes")

    # Event log backend selection (Phase 4b.7).
    #
    # Controls which ``IEventLogRepository`` implementation the engine
    # uses for the shadow write-ahead event log:
    #
    #   "memory" (default) → MemoryEventLogRepository. Ephemeral, lost
    #                        on process restart. Sufficient for tests,
    #                        mock infra, and deployments that don't yet
    #                        need event-sourced replay.
    #   "sqlite"           → SqliteEventLogRepository. Durable, events
    #                        survive process restart, dedupe via SQL
    #                        unique index. Required if any read-side
    #                        feature is backed by the Phase 4c.2 cached
    #                        projector view store.
    #
    # The default stays "memory" so existing deployments see no
    # behavior change. Enabling "sqlite" is a prerequisite for flipping
    # the projector read model on.
    EVENT_LOG_BACKEND: str = os.getenv("EVENT_LOG_BACKEND", "memory")

    # Where GET /nodes/{id}/output reads a node's descriptor from — step 4 of
    # solace/specs/wcp-node-output-storage-redesign.md. Staged exactly like the
    # projector migration was, and for the same reason: flipping a read path
    # blind is how a silently-wrong value reaches the UI.
    #
    #   "event"  (default) — state_updates, as always. The table is not queried.
    #   "shadow"           — read both, log divergence, serve the event value.
    #   "table"            — serve AIW_RUN_NODE_OUTPUT, falling back to the
    #                        event for runs written before it existed.
    #
    # Anything else is treated as "event": a typo must not flip a read path.
    # Default stays "event" until shadow mode has reported clean on real
    # traffic — the table held zero rows when this shipped.
    NODE_OUTPUT_READ_MODE: str = os.getenv("NODE_OUTPUT_READ_MODE", "event")

    # Where a loaded run's ``context.loop_state`` comes from — the last gate on
    # DROP CONTEXT (§15.8 of the same spec). Same three stages, same reason.
    #
    #   "context" (default) — the CONTEXT NCLOB, as always. No extra query.
    #   "shadow"            — read both, log divergence, serve CONTEXT.
    #   "table"             — serve AIW_RUN_LOOP_STATE, falling back to CONTEXT
    #                         when the table has no rows for the run.
    #
    # Anything else is treated as "context": a typo must not flip a read path.
    #
    # The stakes here are higher than for node output, which is why the fallback
    # is unconditional rather than clever. A run that loads with the WRONG
    # loop_state does not error — the loop stalls, or a BREAK lands on the wrong
    # loop. Both are silent.
    LOOP_STATE_READ_MODE: str = os.getenv("LOOP_STATE_READ_MODE", "context")

    # The same three stages for node_visits / pending_revisits —
    # AIW_RUN_NODE_STATE (migrations 055 / 058), §13.1 of the same spec.
    #
    # A SEPARATE switch from LOOP_STATE_READ_MODE on purpose. node_visits sits on
    # the task-id path: TaskFactory.create_task mints {run}-{node}-v{n} from it,
    # so a wrong value re-mints v0 and the event log's (run_id, event_id) dedupe
    # swallows a cycle's second iteration — the run does not error, it silently
    # skips work. That risk profile is not loop_state's, and the two have to be
    # flippable independently.
    NODE_STATE_READ_MODE: str = os.getenv("NODE_STATE_READ_MODE", "context")

    # SQLite file path when EVENT_LOG_BACKEND=="sqlite". Defaults to
    # ":memory:" so the SQLite backend can be enabled in tests without
    # leaving artifacts on disk. Production deployments should set this
    # to a writable filesystem path.
    EVENT_LOG_DB_PATH: str = os.getenv("EVENT_LOG_DB_PATH", ":memory:")

    # Read-model source. Phase 3b.3 collapsed the former tri-state
    # ``PROJECTOR_READ_MODE`` (``live`` / ``shadow`` / ``projector``)
    # into a single path: ``CachedProjectorRunContextViewStore`` backed
    # by the configured event log. ``EVENT_LOG_BACKEND=sqlite`` or
    # ``hana`` is required for durability across process restarts;
    # ``memory`` still works for tests at the cost of rebuild-from-empty
    # on restart.

    # Dispatcher routing mode. Phase 3a PR 2 removed the engine's inline
    # ``_route_to_successors`` edge emission, so the DispatcherConsumer
    # is the only authoritative source. Accepted values:
    #   ``"dispatcher"`` (default) — normal operation. Consumer
    #                                 publishes ``EdgeTraversed`` /
    #                                 ``NodeInput`` / ``WireSkipped`` /
    #                                 ``WireDeadLetter`` via the bus.
    #                                 Task creation + enqueue still run
    #                                 in-process in the engine (Phase 3
    #                                 Executor extraction will move
    #                                 those). Loop and child-workflow
    #                                 edge emissions remain inline —
    #                                 scoped to Phase 3a.3.
    #   ``"shadow"``               — Consumer publishes authoritatively
    #                                 AND records emissions to the parity
    #                                 recorder for diagnostic inspection.
    #                                 Retained for one release as a
    #                                 safety net while operators gain
    #                                 confidence in dispatcher-only
    #                                 fan-out.
    #   ``"inline"``               — Legacy value. Silently upgraded to
    #                                 ``"dispatcher"`` with a warning.
    #                                 Inline engine emission was deleted
    #                                 in Phase 3a PR 2; ``inline`` can
    #                                 no longer produce edge events.
    # Unknown values also upgrade to ``"dispatcher"`` with a warning.
    DISPATCHER_MODE: str = (
        os.getenv("DISPATCHER_MODE", "dispatcher") or "dispatcher"
    ).lower()

    # Event-driven LOOP — RETIRED in Phase 4 Sub 4.3 (2026-05-08).
    #
    # The flag is kept on ``Settings`` so existing ``.env`` files and
    # CF / k8s manifests that set it don't error out, but no runtime
    # path consults the value any more. The
    # ``EventDrivenLoopDriver`` is always wired in ``bootstrap.py``;
    # the legacy ``_run_loop_iterations`` imperative walker has been
    # replaced with a thin enqueue-and-return shim that hands control
    # to the queue worker.
    #
    # Removal target: a future config-cleanup PR after the deprecation
    # window closes.
    EVENT_DRIVEN_LOOP: bool = os.getenv(
        "EVENT_DRIVEN_LOOP", "true",
    ).lower() in ("1", "true", "yes")

    UNIFORM_EXECUTION: bool = os.getenv(
        "UNIFORM_EXECUTION", "true",
    ).lower() in ("1", "true", "yes")

    # Global cap on the total number of in-flight loop iterations
    # across ALL loops in a single run. Prevents resource exhaustion
    # from deeply nested or wide parallel loops. Default: 100.
    MAX_CONCURRENT_ITERATIONS_PER_RUN: int = int(os.getenv(
        "MAX_CONCURRENT_ITERATIONS_PER_RUN", "100",
    ))

    # SA-1889 cutover: v2 node-catalog is the DEFAULT palette source. A deployment
    # can still force it off with ENABLE_NODE_CATALOG_V2=false. Legacy
    # /api/v1/node-palette stays 200 but is deprecated (Sunset headers); its removal
    # is a follow-up once the base-worker picker migrates and the legacy counter flat-lines.
    ENABLE_NODE_CATALOG_V2: bool = os.getenv(
        "ENABLE_NODE_CATALOG_V2", "true",
    ).lower() in ("1", "true", "yes")

    # Phase 3: per-tenant rollout allowlist for v2. Takes effect only when
    # ENABLE_NODE_CATALOG_V2 is true. Empty string (default) means "all
    # tenants served". Comma-separated tenant_ids restrict the endpoints
    # to an allowlist — other tenants get 404 as if v2 were off.
    # Example rollout: "" → "acme,beta-co" → "acme,beta-co,gamma" → "".
    NODE_CATALOG_V2_TENANT_ALLOWLIST: str = os.getenv(
        "NODE_CATALOG_V2_TENANT_ALLOWLIST", "",
    )

    # Phase 1: when true, WorkerNodeProvider raises on malformed FormField
    # in a registered worker's input/output schema. When false (default),
    # malformed entries are dropped with a warning. Phase 3 flips true.
    STRICT_CONTRACT_VALIDATION: bool = os.getenv(
        "STRICT_CONTRACT_VALIDATION", "false",
    ).lower() in ("1", "true", "yes")

    # Phase M.3: stable identifier for this process. Used as the response-
    # topic suffix so an instance that enqueued an EditNodeDataAction can
    # receive the worker's response even when the action is consumed by a
    # different instance (partition-by-run_id routing in Kafka mode).
    # Default: random UUID generated at import time — override with
    # INSTANCE_ID env var for deterministic test harnesses.
    INSTANCE_ID: str = os.getenv("INSTANCE_ID", "")

    # PATCH /data orphan-GC toggle. Default ``true`` — every PATCH that
    # supersedes a file_id schedules ``delete_file`` on the prior version
    # so the file service doesn't accumulate dead bytes. Set to ``false``
    # for browser-driven debugging/testing where you want every
    # intermediate file to remain queryable (pre-provisioned __root__,
    # PATCH pre-images, multi-op intermediates). Production must leave
    # this on.
    ORPHAN_GC_ENABLED: bool = os.getenv(
        "ORPHAN_GC_ENABLED", "true",
    ).lower() in ("1", "true", "yes")

    # Phase 4 — Projector snapshot store.
    #
    # ``off`` (default): no snapshot writes, no snapshot reads. Cold-start
    #                    behaviour identical to pre-Phase-4 (full replay).
    # ``shadow``:        snapshots are written at threshold crossings, but
    #                    cold start still does a full replay. Verification
    #                    harness compares snapshot-based replay vs. full
    #                    replay and logs drift. Intended for soak.
    # ``authoritative``: cold start loads the latest snapshot and replays
    #                    only the tail (``sequence > snapshot.sequence``).
    #                    Flip target once shadow-mode drift=0 over the
    #                    soak window.
    SNAPSHOT_MODE: str = (
        os.getenv("SNAPSHOT_MODE", "off") or "off"
    ).lower()

    # Snapshot every N events folded (whichever trigger fires first
    # with SNAPSHOT_EVERY_T_SECONDS). 100 catches high-throughput runs
    # before the log grows beyond a cheap replay budget.
    SNAPSHOT_EVERY_N_EVENTS: int = int(os.getenv("SNAPSHOT_EVERY_N_EVENTS", "100"))

    # Snapshot every T seconds if at least one event has been folded
    # since the last snapshot. 300s (5 min) catches slow long-lived
    # runs — HITL waits, queued-mode loops — that would never hit the
    # N-events threshold before a pod restart.
    SNAPSHOT_EVERY_T_SECONDS: int = int(os.getenv("SNAPSHOT_EVERY_T_SECONDS", "300"))

    # Retention — keep this many snapshots per run (current + K-1 fallbacks).
    # On each write, older snapshots beyond this count are dropped.
    # K=2 is the minimum safe value: one current + one fallback for the
    # rare case where the current snapshot is corrupted mid-write.
    SNAPSHOT_KEEP_K_BACK: int = int(os.getenv("SNAPSHOT_KEEP_K_BACK", "2"))

    # G-guard: reject task callbacks from a superseded run generation.
    #
    # ``off``    — no check. Tasks stamped at creation, but complete/fail
    #              accept every callback regardless of stamp.
    # ``shadow`` — check and log + metric on mismatch, but do NOT reject.
    #              Use during soak to observe rate of would-be rejections.
    # ``on``     — enforce: return TASK_SUPERSEDED (409) on stale stamp.
    #
    # Tri-state mirrors SNAPSHOT_MODE and the former PROJECTOR_READ_MODE
    # promotion pattern. Default ``on`` — this is a correctness fix, not
    # a feature: mismatches only occur on re-execute of an in-flight
    # test run, which was a latent corruption path before the guard.
    GENERATION_GUARD_MODE: str = (
        os.getenv("GENERATION_GUARD_MODE", "on") or "on"
    ).lower()

    # Phase 3 Sub 3.3 — DATA_EDIT outbox migration. When ``on``, the
    # ``edit_node_data`` use case routes upload / delete side-effects
    # through ``OutboxRequested`` and waits for ``OutboxResolved``
    # rather than calling the file service inline. Default ``off``
    # preserves the pre-Sub-3.3 path; flip to ``on`` in staging for
    # the soak window described in
    # ``docs/design/phase-3-executor-extraction.md`` §13.2.
    OUTBOX_DATA_EDIT: bool = os.getenv(
        "OUTBOX_DATA_EDIT", "false",
    ).lower() in ("1", "true", "yes")

    # Phase 3 Sub 3.3 — sync-wait budget for the outbox awaiter.
    # When the dispatcher takes longer than this to resolve a request,
    # the producer falls back to its current direct-call path so the
    # HTTP request still returns within the SLA. Sub 3.5 will replace
    # the fallback with a 202 Accepted response model; until then the
    # safe move is to pretend the outbox was off.
    OUTBOX_HTTP_WAIT_TIMEOUT_S: float = float(
        os.getenv("OUTBOX_HTTP_WAIT_TIMEOUT_S", "5.0"),
    )

    # Phase 3 Sub 3.2 — outbox dispatcher poll cadence.
    OUTBOX_DISPATCHER_POLL_INTERVAL_S: float = float(
        os.getenv("OUTBOX_DISPATCHER_POLL_INTERVAL_S", "1.0"),
    )

    # Phase 3 Sub 3.2 — retry budget per outbox entry.
    OUTBOX_MAX_ATTEMPTS: int = int(
        os.getenv("OUTBOX_MAX_ATTEMPTS", "5"),
    )

    # Phase 3 Sub 3.4 — HITL resume migration. When ``on``,
    # ``resume_pending_task`` and ``resume_run`` route their
    # ``stage_recorder.record(...)`` and ``orchestration_queue.enqueue``
    # side-effects through outbox entries (``stage_record`` and
    # ``queue_enqueue`` kinds). Default ``off`` keeps the pre-Sub-3.4
    # direct-call path; flip to ``on`` in staging per the soak window
    # described in ``docs/design/phase-3-executor-extraction.md`` §13.2.
    OUTBOX_HITL_RESUME: bool = os.getenv(
        "OUTBOX_HITL_RESUME", "false",
    ).lower() in ("1", "true", "yes")

    # Executor extraction (Phase 3). Two states:
    #
    #   ``in_process`` (default) — engine invokes node handlers
    #                              in-process; the ExecutorConsumer is
    #                              wired but ``start()`` is a no-op.
    #   ``executor``             — the standalone ExecutorConsumer is the
    #                              authoritative handler invoker. Engine
    #                              emits ``NodeInput`` and returns. Gate 3
    #                              (Phase 3 doc §13.4); the inline-deletion
    #                              surgery to reach drift=0 lands as a
    #                              follow-up.
    #
    # Unknown values fall back to ``in_process`` with a warning.
    EXECUTOR_MODE: str = (
        os.getenv("EXECUTOR_MODE", "in_process") or "in_process"
    ).lower()

    # Durable timers (SA-1371). Pending-timer store backend, selected like
    # ``EVENT_LOG_BACKEND``: ``memory`` (default) / ``sqlite`` / ``hana``.
    # ``sqlite`` uses ``TIMER_DB_PATH``; ``hana`` reuses the shared
    # ``HanaConnectionManager`` at ``infra.db`` (falls back to memory when
    # unavailable).
    TIMER_BACKEND: str = os.getenv("TIMER_BACKEND", "memory")
    TIMER_DB_PATH: str = os.getenv("TIMER_DB_PATH", ":memory:")

    # Inbound webhook registrations + deliveries store (SA-2059 F2). Memory
    # (default) loses registrations on restart; ``hana`` reuses the shared
    # ``HanaConnectionManager`` at ``infra.db`` for durability across restarts
    # (falls back to memory with a warning when unavailable).
    WEBHOOK_BACKEND: str = os.getenv("WEBHOOK_BACKEND", "memory")

    # Durable timers (SA-1371). The leader-gated TimerDispatcher fires due
    # timers. Firing is idempotent (deterministic event_id + event-log
    # dedupe), but the v1 SingleOwnerLease is always-leader, so enable the
    # dispatcher on EXACTLY ONE pod. Default ``false`` keeps the loop dormant.
    TIMER_DISPATCHER_ENABLED: bool = os.getenv(
        "TIMER_DISPATCHER_ENABLED", "false",
    ).lower() in ("1", "true", "yes")

    # TimerDispatcher poll cadence (seconds) when the last tick was empty.
    TIMER_DISPATCHER_POLL_INTERVAL_S: float = float(
        os.getenv("TIMER_DISPATCHER_POLL_INTERVAL_S", "1.0"),
    )

    # Durable timers (SA-1371 S4). The TimerFiredConsumer subscribes to
    # ``timer.fired`` and resumes a parked WAIT node when its timer fires.
    # Consuming is a separate role from firing (the leader fires; every pod may
    # consume), so it has its own switch — default ``false`` keeps it dormant.
    TIMER_RESUME_CONSUMER_ENABLED: bool = os.getenv(
        "TIMER_RESUME_CONSUMER_ENABLED", "false",
    ).lower() in ("1", "true", "yes")

    # Event triggers (SA-2059). The EventTriggerConsumer subscribes to
    # ``workflow.triggers`` and starts a run for every workflow whose Event
    # trigger matches the event's topic (1→N), deduped by a derived run id.
    # Multi-instance safe with NO leader gate — competing consumers on the
    # shared queue deliver each message once — so every pod may run it. Needs
    # a broker; default ``false`` keeps it dormant until rollout.
    EVENT_TRIGGER_CONSUMER_ENABLED: bool = os.getenv(
        "EVENT_TRIGGER_CONSUMER_ENABLED", "false",
    ).lower() in ("1", "true", "yes")

    # Smartsheet event trigger (smartsheet-event-trigger). A poller drains the
    # org-wide Event Reporting API (GET /2.0/events) and republishes each event
    # onto ``workflow.triggers``, reusing the EventTriggerConsumer downstream.
    # Needs a broker, the consumer enabled, and a System Admin token. Default
    # ``false`` keeps it dark until a sysadmin token is provisioned.
    SMARTSHEET_POLLER_ENABLED: bool = os.getenv(
        "SMARTSHEET_POLLER_ENABLED", "false",
    ).lower() in ("1", "true", "yes")
    # System Admin API token (Event Reporting requires it). Bind from a secret/
    # vault — never commit. Empty → poller stays dormant even if enabled.
    SMARTSHEET_TOKEN: str = os.getenv("SMARTSHEET_TOKEN", "")
    # Tenant stamped on emitted envelopes (Event Mesh is per-tenant on BTP).
    SMARTSHEET_TENANT_ID: str = os.getenv("SMARTSHEET_TENANT_ID", "system")
    # Poll cadence (seconds). Keep well under Smartsheet's ~7-day event retention
    # so a paused poller never misses events.
    SMARTSHEET_POLL_INTERVAL_SECONDS: float = float(
        os.getenv("SMARTSHEET_POLL_INTERVAL_SECONDS", "300"),
    )
    # First-drain lookback (hours), bounded by the ~7-day retention; the cursor
    # resumes thereafter.
    SMARTSHEET_BOOTSTRAP_LOOKBACK_HOURS: int = int(
        os.getenv("SMARTSHEET_BOOTSTRAP_LOOKBACK_HOURS", "24"),
    )

    # Per-workflow storm control for the EventTriggerConsumer (SA-2065).
    EVENT_TRIGGER_MAX_STARTS_PER_MINUTE: int = int(
        os.getenv("EVENT_TRIGGER_MAX_STARTS_PER_MINUTE", "60"),
    )
    EVENT_TRIGGER_MAX_CONCURRENT_STARTS: int = int(
        os.getenv("EVENT_TRIGGER_MAX_CONCURRENT_STARTS", "8"),
    )

    model_config = {"env_file": ".env", "extra": "allow"}


settings = Settings()
