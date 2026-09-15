import logging
import os

from pydantic_settings import BaseSettings

from .vcap_util import get_event_mesh_credentials

logger = logging.getLogger(__name__)

# Event Mesh credentials — VCAP_SERVICES takes priority over env vars so a CF
# service binding (e.g. ``simplemdg-event-core-3``) is auto-discovered without
# explicit deployment.yml wiring. Without this, an empty token_url surfaces
# as an httpx UnsupportedProtocol error inside _refresh_token at runtime.
_em_creds = get_event_mesh_credentials()
if _em_creds:
    logger.info("Using Event Mesh credentials from VCAP_SERVICES")
    _EVENT_MESH_TOKEN_URL = _em_creds.token_url
    _EVENT_MESH_MESSAGING_URL = _em_creds.messaging_url
    _EVENT_MESH_MANAGEMENT_URL = _em_creds.management_url
    _EVENT_MESH_CLIENT_ID = _em_creds.client_id
    _EVENT_MESH_CLIENT_SECRET = _em_creds.client_secret
    _EVENT_MESH_NAMESPACE = _em_creds.namespace
    _EVENT_MESH_AMQP_URL = getattr(_em_creds, "amqp_url", "")
else:
    _EVENT_MESH_TOKEN_URL = os.getenv("EVENT_MESH_TOKEN_URL", "")
    _EVENT_MESH_MESSAGING_URL = os.getenv("EVENT_MESH_MESSAGING_URL", "")
    _EVENT_MESH_MANAGEMENT_URL = os.getenv("EVENT_MESH_MANAGEMENT_URL", "")
    _EVENT_MESH_CLIENT_ID = os.getenv("EVENT_MESH_CLIENT_ID", "")
    _EVENT_MESH_CLIENT_SECRET = os.getenv("EVENT_MESH_CLIENT_SECRET", "")
    _EVENT_MESH_NAMESPACE = os.getenv("EVENT_MESH_NAMESPACE", "")
    _EVENT_MESH_AMQP_URL = os.getenv("EVENT_MESH_AMQP_URL", "")


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "allow"}

    # --- App ---
    APP_NAME: str = "Worker Execution Service"
    APP_MODE: str = os.getenv("APP_MODE", "REST")

    # Infrastructure mode toggle: "mock" | "local" | "sap"
    INFRA_MODE: str = os.getenv("INFRA_MODE", "mock")

    # --- Service-specific ---
    SERVICE_NAME: str = "worker-execution"
    SERVICE_PORT: int = int(os.getenv("SERVICE_PORT", "8004"))
    KAFKA_TASK_TOPIC: str = os.getenv("KAFKA_TASK_TOPIC", "workflow.tasks")
    KAFKA_RESULT_TOPIC: str = os.getenv("KAFKA_RESULT_TOPIC", "workflow.results")
    INTEGRATION_SERVICE_URL: str = os.getenv("INTEGRATION_SERVICE_URL", "http://localhost:8002")
    WORKFLOW_CONTROL_PLANE_URL: str = os.getenv("WORKFLOW_CONTROL_PLANE_URL", "http://localhost:8001")
    WAIT_WORKER_URL: str = os.getenv("WAIT_WORKER_URL", "http://localhost:8006")
    HTTP_REQUEST_WORKER_URL: str = os.getenv("HTTP_REQUEST_WORKER_URL", "http://localhost:8007")

    # --- SAP HANA ---
    HANA_HOST: str = os.getenv("HANA_HOST", "")
    HANA_PORT: int = int(os.getenv("HANA_PORT", "443"))
    HANA_USERNAME: str = os.getenv("HANA_USERNAME", "")
    HANA_PASSWORD: str = os.getenv("HANA_PASSWORD", "")
    HANA_SCHEMA: str = os.getenv("HANA_SCHEMA", "")
    HANA_ENCRYPT: str = os.getenv("HANA_ENCRYPT", "true")
    HANA_SSL_CERT: str = os.getenv("HANA_SSL_CERT", "")
    HANA_POOL_SIZE: int = int(os.getenv("HANA_POOL_SIZE", "5"))
    HANA_MAX_OVERFLOW: int = int(os.getenv("HANA_MAX_OVERFLOW", "10"))
    HANA_POOL_RECYCLE: int = int(os.getenv("HANA_POOL_RECYCLE", "3600"))

    # --- Messaging ---
    MESSAGING_MODE: str = os.getenv("MESSAGING_MODE", "")
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    KAFKA_GROUP_ID: str = os.getenv("KAFKA_GROUP_ID", "ai-workflow")
    KAFKA_AUTO_OFFSET_RESET: str = os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest")

    # --- SAP Event Mesh (resolved at module import — see top of file) ---
    EVENT_MESH_TOKEN_URL: str = _EVENT_MESH_TOKEN_URL
    EVENT_MESH_MESSAGING_URL: str = _EVENT_MESH_MESSAGING_URL
    EVENT_MESH_MANAGEMENT_URL: str = _EVENT_MESH_MANAGEMENT_URL
    EVENT_MESH_CLIENT_ID: str = _EVENT_MESH_CLIENT_ID
    EVENT_MESH_CLIENT_SECRET: str = _EVENT_MESH_CLIENT_SECRET
    EVENT_MESH_NAMESPACE: str = _EVENT_MESH_NAMESPACE

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # --- Async dispatch ---
    CALLBACK_BASE_URL: str = os.getenv("CALLBACK_BASE_URL", "")
    TASK_TIMEOUT_SECONDS: int = int(os.getenv("TASK_TIMEOUT_SECONDS", "120"))
    # SA-1531: per-type pending-timeout overrides, JSON {worker_type: seconds}.
    #
    # The key is the worker's ``WORKER_TYPE`` env value — the same string that
    # arrives on the task and is registered with the executor — NOT the CF
    # application name. The two differ for every worker in the fleet:
    #
    #     app name            WORKER_TYPE (the key to use here)
    #     agent-worker        agent
    #     code-worker         code
    #     http-request-worker http_request
    #
    # Getting this wrong is silent: ``_task_timeout_for`` does a plain dict
    # lookup with a fallback, so a map keyed by app name never matches and every
    # type quietly takes TASK_TIMEOUT_SECONDS. The dev deployment shipped
    # {"agent-worker": 600, "code-worker": 300} and therefore ran the agent
    # worker on 120s, not 600s, for the entire life of the flag.
    #
    # Empty = all types use TASK_TIMEOUT_SECONDS.
    TASK_TIMEOUT_OVERRIDES: str = os.getenv("TASK_TIMEOUT_OVERRIDES", "")
    # Head-room added on top of a deadline the TASK itself declares (``timeout_ms``,
    # or a ``timeout_seconds`` input) before the sweeper gives up on the callback.
    # It has to cover dispatch + the worker's own request teardown + the callback
    # POST; 30s is generous for all three. The executor must never fire first —
    # a sweep that beats the worker's own deadline reports WORKER_TIMEOUT on a
    # task that was about to succeed.
    TASK_CALLBACK_GRACE_SECONDS: int = int(os.getenv("TASK_CALLBACK_GRACE_SECONDS", "30"))
    # Absolute ceiling for a resolved pending timeout. A task-declared deadline is
    # caller-supplied data, so it is clamped here: without a ceiling one node with
    # timeout_seconds=999999 would pin a concurrency slot for eleven days.
    TASK_TIMEOUT_MAX_SECONDS: int = int(os.getenv("TASK_TIMEOUT_MAX_SECONDS", "3600"))
    SWEEP_INTERVAL_SECONDS: int = int(os.getenv("SWEEP_INTERVAL_SECONDS", "30"))

    # --- Worker liveness sweeper (SA-2051, R4) ---
    # A registration silent longer than STALE is marked unhealthy (so it drops out
    # of count_healthy / get_by_worker_type / dispatch); silent longer than REMOVE
    # is garbage-collected. Runs on the shared SWEEP_INTERVAL_SECONDS tick — a
    # distinct cadence is deliberately NOT added (spec §7). The threshold pair, not
    # the poll cadence, governs liveness detection.
    WORKER_STALE_AFTER_SECONDS: int = int(os.getenv("WORKER_STALE_AFTER_SECONDS", "120"))
    WORKER_REMOVE_AFTER_SECONDS: int = int(os.getenv("WORKER_REMOVE_AFTER_SECONDS", "600"))

    # --- Concurrency ---
    # Maximum number of tasks the executor will accept at once. Default
    # 100 is sized for a 512 MB executor pod:
    #   baseline (Python + FastAPI + broker client + registry + pending
    #   task store + trace store) ≈ 210 MB, leaving ~300 MB. Each
    #   PendingTaskEntry holds the payload + node_config + idempotency
    #   key — typically ~50 KB. 100 in-flight tasks ≈ 5 MB of pending
    #   state, well within the budget. The cap is application-level
    #   backpressure rather than a memory ceiling — raise it (e.g. 200,
    #   500) when downstream workers can absorb more parallel dispatch,
    #   lower it when the executor process is memory-constrained.
    # Tasks exceeding the cap get a CONCURRENCY_LIMIT error so callers
    # know to back off. The previous hard-coded ceiling was 10.
    MAX_CONCURRENT_TASKS: int = int(os.getenv("MAX_CONCURRENT_TASKS", "100"))

    # --- Pull delivery (SA-2025) ---
    # Master feature gate. When false (default) the pull ready-task store is not
    # created, so ExecuteTaskUseCase receives ready_task_store=None and the pull
    # branch is skipped entirely — the push path is byte-for-byte unchanged.
    # Flip to true only where pull workers are deployed; a push-only executor
    # never pays for the feature.
    PULL_DELIVERY_ENABLED: bool = str(
        os.getenv("PULL_DELIVERY_ENABLED", "false")
    ).lower() in ("1", "true", "yes", "on")
    # Lease (visibility-timeout) config. LEASE_TTL_SECONDS is how long a claimed
    # task stays invisible before the sweeper re-enqueues it (B8); per-type
    # overrides mirror TASK_TIMEOUT_OVERRIDES. LEASE_MAX_WAIT_SECONDS caps the
    # long-poll hold; LEASE_MAX_BATCH caps tasks per lease call.
    LEASE_TTL_SECONDS: int = int(os.getenv("LEASE_TTL_SECONDS", "60"))
    # Pull sweeper (B8). A lease that expires re-enqueues with exponential backoff
    # up to MAX_LEASE_ATTEMPTS, after which the task is POISONED (terminal + DLQ).
    # A READY task never leased within LEASE_MAX_READY_SECONDS → WORKER_UNAVAILABLE.
    # 0 = off (MAX_LEASE_ATTEMPTS=0 → unbounded retries; LEASE_MAX_READY_SECONDS=0 →
    # no stale-ready reaping).
    MAX_LEASE_ATTEMPTS: int = int(os.getenv("MAX_LEASE_ATTEMPTS", "5"))
    LEASE_BACKOFF_BASE_SECONDS: float = float(os.getenv("LEASE_BACKOFF_BASE_SECONDS", "2"))
    LEASE_MAX_READY_SECONDS: int = int(os.getenv("LEASE_MAX_READY_SECONDS", "900"))
    LEASE_TTL_OVERRIDES: str = os.getenv("LEASE_TTL_OVERRIDES", "")
    LEASE_MAX_WAIT_SECONDS: float = float(os.getenv("LEASE_MAX_WAIT_SECONDS", "20"))
    LEASE_MAX_BATCH: int = int(os.getenv("LEASE_MAX_BATCH", "16"))
    # Hard ceiling on in-flight LEASED tasks across all partitions (B4). Bounds the
    # leased set so a flood can't grow it without limit; a lease at the ceiling
    # returns [] (backpressure). 0 = unlimited. B6 extends to a byte-quota hierarchy.
    MAX_TOTAL_LEASED: int = int(os.getenv("MAX_TOTAL_LEASED", "500"))

    # --- Pull admission byte quotas (B6). 0 = unlimited (default → no change). ---
    # Bound the untrusted-write bytes held in the pull queue as a hierarchy
    # (partition ⊂ tenant ⊂ global). An enqueue that would overflow any level is
    # refused with Event-Mesh backpressure (redelivery), never a 429/silent drop.
    MAX_READY_BYTES_GLOBAL: int = int(os.getenv("MAX_READY_BYTES_GLOBAL", "0"))
    MAX_READY_BYTES_PER_TENANT: int = int(os.getenv("MAX_READY_BYTES_PER_TENANT", "0"))
    MAX_READY_BYTES_PER_PARTITION: int = int(os.getenv("MAX_READY_BYTES_PER_PARTITION", "0"))
    # Cap on concurrent long-poll lease connections (in-process). 0 = unlimited.
    MAX_LEASE_CONNECTIONS: int = int(os.getenv("MAX_LEASE_CONNECTIONS", "0"))
    # Cap on total registered workers (registry OOM vector). 0 = unlimited.
    MAX_REGISTERED_WORKERS: int = int(os.getenv("MAX_REGISTERED_WORKERS", "0"))

    # --- Event Mesh consumer poll loop (sap mode) ---
    # The broker consumer drains up to BROKER_POLL_BATCH_SIZE messages per
    # queue per pass (back-to-back, no inter-message delay) and only sleeps
    # BROKER_POLL_IDLE_SLEEP_SECONDS when every queue is empty. The previous
    # loop consumed one message per cycle and slept 0.5s unconditionally,
    # capping ingest at ~2 msg/s regardless of MAX_CONCURRENT_TASKS.
    BROKER_POLL_BATCH_SIZE: int = int(os.getenv("BROKER_POLL_BATCH_SIZE", "50"))
    BROKER_POLL_IDLE_SLEEP_SECONDS: float = float(
        os.getenv("BROKER_POLL_IDLE_SLEEP_SECONDS", "0.5")
    )
    # Competing-consumer threads per instance (parallelize consume+handle+ack),
    # and the per-consume message lock TTL (must exceed dispatch+ack time so
    # concurrent processing doesn't trigger spurious redelivery).
    BROKER_CONSUMER_THREADS: int = int(os.getenv("BROKER_CONSUMER_THREADS", "4"))
    BROKER_LOCK_DURATION_MS: int = int(os.getenv("BROKER_LOCK_DURATION_MS", "60000"))
    # Redeliveries before a message is dead-lettered. Deliberately far above
    # the control plane's 5: here a redelivery is also the BACKPRESSURE rail —
    # a saturated executor skips the ack so the broker re-offers the task —
    # and at a 60s lock TTL a cap of N is roughly N minutes of sustained
    # saturation before a perfectly healthy task is dead-lettered instead.
    BROKER_MAX_REDELIVERY_COUNT: int = int(os.getenv("BROKER_MAX_REDELIVERY_COUNT", "50"))

    # --- Event Mesh consume transport (sap mode) ---
    # "amqp" uses a push-based AMQP 1.0 (over WebSocket) consumer that removes
    # the per-instance 429s from polling the REST consumption endpoint; "rest"
    # keeps the legacy poll pool. AMQP auto-falls-back to REST when the libs are
    # missing or the endpoint is unreachable, so "amqp" is safe to default.
    EVENT_MESH_CONSUME_TRANSPORT: str = os.getenv("EVENT_MESH_CONSUME_TRANSPORT", "amqp")
    EVENT_MESH_AMQP_URL: str = _EVENT_MESH_AMQP_URL or os.getenv("EVENT_MESH_AMQP_URL", "")
    EVENT_MESH_AMQP_WS_PATH: str = os.getenv("EVENT_MESH_AMQP_WS_PATH", "/protocols/amqp10ws")
    # 10, not 50: the executor signals backpressure by NOT acking, so every
    # prefetched message is one the broker already considers delivered and will
    # not offer to an instance that does have capacity.
    EVENT_MESH_AMQP_PREFETCH: int = int(os.getenv("EVENT_MESH_AMQP_PREFETCH", "10"))
    # 0 = match BROKER_CONSUMER_THREADS (competing receiver links; tasks aren't
    # order-sensitive and dedup makes redelivery safe).
    EVENT_MESH_AMQP_CONCURRENCY: int = int(os.getenv("EVENT_MESH_AMQP_CONCURRENCY", "0"))
    EVENT_MESH_AMQP_STRIP_NAMESPACE: str = os.getenv("EVENT_MESH_AMQP_STRIP_NAMESPACE", "0")

    # gRPC transport (coexists with HTTP — "http" | "grpc")
    TRANSPORT_MODE: str = os.getenv("TRANSPORT_MODE", "http")
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", "50052"))
    GRPC_MAX_WORKERS: int = int(os.getenv("GRPC_MAX_WORKERS", "10"))
    WORKFLOW_CONTROL_PLANE_GRPC_TARGET: str = os.getenv("WORKFLOW_CONTROL_PLANE_GRPC_TARGET", "localhost:50051")

    # Phase 4 of the RESULT_TOO_LARGE work. The executor's third outbound
    # dependency, used on exactly one path: a worker result too large to publish
    # is uploaded here and relayed as a pointer instead of being thrown away
    # (run edda4f82-4a09-4587-beb5-8ed615ab8219 did 10.7 s of real work and then
    # lost the answer because it would not fit on the wire).
    #
    # EMPTY BY DEFAULT, and that is the off switch: bootstrap only builds the
    # uploader when this is set, smart-DI drops None dependencies, and the use
    # case falls back to the pre-existing task.failed degrade. So an executor
    # that has not been given a file service behaves exactly as it did before.
    FILE_SERVICE_URL: str = os.getenv("FILE_SERVICE_URL", "")


settings = Settings()
