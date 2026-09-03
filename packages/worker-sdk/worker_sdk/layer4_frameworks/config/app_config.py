from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" is load-bearing, not cosmetic. This class is instantiated at import
    # time (the ``settings`` singleton at the bottom of this module), and pydantic-settings
    # defaults to extra="forbid". The dotenv source hands over EVERY key in the ``.env``
    # file — not just the ones declared here — so a single unrecognised key (a worker's own
    # credential, an operator's scratch value, a setting added by a newer worker) aborted
    # the whole process with "Extra inputs are not permitted" before any worker code ran.
    # Real environment variables never had this problem: EnvSettingsSource only looks up
    # declared fields, which is why `export FOO=…` worked while the same key in `.env` did
    # not. Trade-off accepted knowingly: a typo'd setting name in `.env` (SERVER_PORTT) is
    # now silently ignored rather than reported. Workers that need extra settings declare
    # them on their own Settings subclass and read them off that instance.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Worker SDK"
    APP_MODE: str = "SERVER"

    # SDK protocol version
    SDK_VERSION: str = "1.0.0"

    # Worker identity
    WORKER_TYPE: str = "generic"
    WORKER_VERSION: str = "0.1.0"
    WORKER_NAME: str = ""
    WORKER_DESCRIPTION: str = ""

    # Node metadata (aligned with workflow-control-plane node schema)
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "Cog"
    WORKER_COLOR: str = "#3B82F6"
    WORKER_TAGS: str = ""

    # Registry
    REGISTRY_URL: str = "http://localhost:8000"

    # Heartbeat
    HEARTBEAT_INTERVAL_SECONDS: int = 30

    # --- Pull delivery (SA-2028) — used only when APP_MODE=PULL ---
    # The worker registers delivery_mode=pull and long-polls the executor's lease
    # endpoint instead of exposing an HTTP server. WORKER_TENANT scopes the lease
    # to a (tenant, worker_type) partition (B5 will derive it from the XSUAA
    # subject; until then it's an explicit config).
    WORKER_DELIVERY_MODE: str = "push"
    WORKER_TENANT: str = ""
    LEASE_MAX_BATCH: int = 4          # tasks to request per lease call
    LEASE_WAIT_SECONDS: float = 20.0  # long-poll hold (<= executor LEASE_MAX_WAIT_SECONDS)
    LEASE_ERROR_BACKOFF_SECONDS: float = 2.0  # sleep after a lease/transport error
    # Minimum gap between empty lease polls — guards against a hot-spin when the
    # executor returns [] immediately (pull not enabled there, or wait=0).
    LEASE_IDLE_FLOOR_SECONDS: float = 1.0
    # Lease renewal for long-running tasks (SA-2029, B4). When enabled, the renew
    # interval is DERIVED from the executor's authoritative lease TTL carried on
    # each lease (interval = lease_ttl_seconds * safety_fraction), so it can never
    # exceed the TTL — no cross-service (SDK vs executor) mis-config is possible.
    # Monotonic interval — immune to worker/executor clock skew.
    LEASE_RENEW_ENABLED: bool = False
    LEASE_RENEW_SAFETY_FRACTION: float = 0.5   # renew at half the TTL
    LEASE_RENEW_MIN_INTERVAL_SECONDS: float = 1.0
    # Absolute wall-clock budget for renewing ONE task's lease. Renewal is driven
    # by "the exec core has not returned yet", which is not the same as "the task
    # is still making progress" — a wedged handler (a socket read with no timeout,
    # a hung subprocess) never returns, so an uncapped renewer extends the lease
    # forever.
    #
    # That is not merely a stuck task: the executor's recovery net is
    # ``sweep_expired_leases``, which only acts on rows where
    # ``state == LEASED and deadline < now``. Renewing past the deadline means
    # that condition is never true, so attempt_count never increments,
    # MAX_LEASE_ATTEMPTS is never reached and the task is never re-enqueued or
    # POISONED. Unbounded renewal disarms B8 entirely.
    #
    # When the budget is spent the worker stops renewing and says so; the lease
    # then expires on its own schedule and the executor's net does its job. The
    # task itself is NOT cancelled (the SDK has no safe way to kill a handler
    # mid-flight) — if it finishes before the last granted deadline its callback
    # is still accepted.
    #
    # Keep this at or above the executor's TASK_TIMEOUT_MAX_SECONDS so a
    # legitimately long task is not cut short by the shorter of the two. <= 0
    # disables the cap and restores the old unbounded behaviour — deliberate
    # opt-out only.
    LEASE_RENEW_MAX_SECONDS: float = 3600.0

    # Server
    SERVER_HOST: str = "localhost"
    SERVER_PORT: int = 35000

    # Public endpoint URL used for worker registration.
    # Set via PROXY_URL env var in cloud deployments (e.g. the CF route).
    # Falls back to http://{SERVER_HOST}:{SERVER_PORT} for local dev.
    PROXY_URL: str = ""

    @model_validator(mode="after")
    def _default_proxy_url(self) -> "Settings":
        if not self.PROXY_URL:
            self.PROXY_URL = f"http://{self.SERVER_HOST}:{self.SERVER_PORT}"
        return self

    # File service
    FILE_SERVICE_URL: str = ""
    RESOLVE_FILE_REFS: bool = True

    # SA-1943 — cap the cumulative bytes of a file-backed INPUT collection the
    # SDK resolver loads into worker RAM. ``_fetch_file_data`` pages a collection
    # via OData but concatenates every page into one list; on a small worker pod
    # (256 MB, ~86 MB free) a large collection OOMs the worker. Above this cap the
    # resolver raises FileRefResolutionError instead of OOMing — a handler that
    # genuinely needs the whole collection must raise this or consume it in
    # bounded windows (true per-row streaming to the handler is a per-handler
    # follow-up, e.g. the not-yet-built mapping-worker Iterate node). 0 = disabled
    # (legacy unbounded accumulate).
    #
    # IMPORTANT: this counts RAW WIRE bytes, but the accumulated ``all_rows``
    # holds PARSED Python objects — a 3-40x blow-up (a ~7-byte ``{"i":1}`` row is
    # a ~300-byte dict). So the resident list at the cap is ~3-40x this value.
    # 16 MiB → ~48-640 MB parsed worst-case; sized so the TYPICAL (3-5x) case
    # stays within an 86 MB-free 256 MB pod. RAISE it on larger pods / bulk
    # workers (512 MB → 32, 1 GB → 64); LOWER it for wide/tiny-row data. Env:
    # RESOLVE_MAX_INPUT_BYTES.
    #
    # 2026-08-18 — TWO CORRECTIONS, both learned the hard way:
    #
    # 1. This default assumes a 256 MB pod, and http-request-worker was deployed
    #    on 128 MB. A default that reasons about twice the memory it has is not
    #    conservative, it is optimistic, and nothing checked. Any worker that
    #    leaves this at the default is making that assumption silently — so
    #    declare it in the worker's OWN manifest, beside its `memory`, where the
    #    two can be checked against each other (http-request-worker now does,
    #    with a test that fails if they move apart).
    #
    # 2. The 3-40x range is for NARROW rows, where per-object overhead dominates
    #    a tiny payload. It does not generalise. Measured on 50 live rows of the
    #    wide shape this fleet actually carries (61 short string columns), the
    #    wire→parsed ratio is **1.3x** — because the wire form repeats every
    #    column name on every row (~1.5 KB/row of key text) while memory shares
    #    the keys. Size from the shape you carry, not from the range.
    RESOLVE_MAX_INPUT_BYTES: int = 16 * 1024 * 1024

    # Hand a large file-backed ARRAY input to the handler as a streaming handle
    # instead of materialising it. The handler then serialises it straight into
    # its outgoing request, so the collection is never one big Python object on
    # its way to being bytes again — peak RAM becomes a function of
    # RESOLVE_WINDOW_ROWS, not of the payload.
    #
    # OFF by default and opt-in per worker: a handle where a handler expects a
    # list is a silent contract change, and only http-request-worker knows how to
    # consume one today.
    #
    # With this on, RESOLVE_MAX_INPUT_BYTES stops being the ceiling on what can be
    # sent — it survives as a PER-WINDOW guard, which is the job it was actually
    # written for (do not let the whole collection land in a small pod).
    WORKER_STREAM_FILE_BACKED_INPUTS: bool = False

    # Root rows per window. One window's rows plus their joined children is the
    # resident set; smaller is safer, larger means fewer round trips.
    RESOLVE_WINDOW_ROWS: int = 500

    # The OUTPUT half of the budget above, which did not exist until run
    # edda4f82-4a09-4587-beb5-8ed615ab8219 (tenant-1, 2026-08-18) failed
    # RESULT_TOO_LARGE: a worker returned 12 980 452 B and the executor could
    # not publish it past the 1 048 576 B Event Mesh cap. Inputs were budgeted,
    # outputs were not, so the first thing that noticed was the broker — on the
    # far side of the wire, with no idea which field was to blame.
    #
    # Default = the broker cap, because that is the real ceiling; a worker whose
    # result exceeds it cannot be delivered no matter what anyone intended.
    RESULT_MAX_OUTPUT_BYTES: int = 1024 * 1024

    # SHADOW BY DEFAULT. Off = measure, log and count an over-budget result but
    # publish it anyway (so turning the measurement on cannot change a single
    # outcome). On = fail the task at the worker, with per-field attribution,
    # instead of letting the executor discover it. Flip only after the shadow
    # numbers say what enforcing would have failed.
    RESULT_BUDGET_ENFORCE: bool = False

    # Logging
    LOG_LEVEL: str = "INFO"
    DISABLE_HTTPX_LOG: bool = True

    # Data I/O
    DATA_INPUT_PATH: str = "/tmp/worker/input"
    DATA_OUTPUT_PATH: str = "/tmp/worker/output"

    # SA-1905 claim-check — stream a large worker output to CSV at the WORKER
    # (via the native json_csv_streamer + the output writer) instead of
    # returning it inline, so a 500 MB output never crosses the wire inline
    # (gRPC ~4 MB / Kafka ~1 MB) nor OOMs the control plane's dict_to_csv.
    # OFF, below threshold, or when the native wheel is absent → inline output,
    # so behaviour is unchanged by default. Mirrors the control-plane flags.
    WORKER_CHUNKED_OUTPUT_ENABLED: bool = False
    CHUNKING_THRESHOLD_BYTES: int = 1024 * 1024  # 1 MB

    # SA-1905 — also write a nested Arrow mirror of the same value beside the
    # CSV. One scan produces both and the CSV bytes are asserted byte-identical
    # to the CSV-only path, so the steward's file is provably untouched. OFF by
    # default; an unread sidecar is inert, so even ON it changes nothing until
    # the control plane's ARROW_READ_MODE is turned up.
    WORKER_ARROW_SIDECAR_ENABLED: bool = False

    # Arrow IPC compression for that sidecar: none | lz4 | zstd. Same name and
    # same default as the control plane's setting, on purpose — one knob, one
    # spelling, whichever tier you are looking at.
    #
    # Default "none" because the WHEEL must land everywhere before any writer is
    # flipped: the control plane is the only READER, and a reader built without
    # the crate's ``ipc_compression`` feature cannot decode a compressed file at
    # all. It would fall through to CSV silently. See
    # solace/specs/wcp-arrow-size-reduction-plan.md 1.6.
    ARROW_SIDECAR_COMPRESSION: str = "none"

    # Phase 3 of the RESULT_TOO_LARGE work — stream a large STRING output the
    # same way a list/dict is streamed: one row in the reserved ``__value``
    # column, declared ``value_kind: "string"``. Until this, a string rode the
    # event inline at any size, which is exactly how run
    # edda4f82-4a09-4587-beb5-8ed615ab8219 put 12.9 MB on a 1 MiB broker.
    #
    # OFF by default, and its own flag rather than a widening of
    # WORKER_CHUNKED_OUTPUT_ENABLED, because a scalar ref is only readable by a
    # worker that carries the matching resolver change. Turning the writer on
    # against an older worker fleet would hand those workers
    # ``{"__value": ...}`` where a string belongs.
    WORKER_STREAM_SCALARS_ENABLED: bool = False

    # gRPC transport (coexists with HTTP — "http" | "grpc")
    TRANSPORT_MODE: str = "http"
    GRPC_PORT: int = 50053
    REGISTRY_GRPC_TARGET: str = "localhost:50052"

    # Maximum threads in the asyncio default executor — bounds concurrent
    # sync handlers (``asyncio.to_thread`` dispatches to this pool).
    # Default 8 is sized for a 256 MB worker pod:
    #   baseline (Python + FastAPI + worker-sdk + httpx + connection
    #   pools) ≈ 170 MB, leaving ~86 MB. Each concurrent sync handler
    #   costs ~10 MB (thread stack 8 MB + working memory). 8 threads ≈
    #   80 MB, fits within the budget with a small margin.
    #
    # Async handlers (``async def`` with ``httpx.AsyncClient``) ignore
    # this setting — they run directly on the event loop without thread
    # hops and effectively have no concurrency ceiling per pod.
    #
    # Raise for larger pods (e.g. 512 MB → 16, 1 GB → 32). Python's own
    # default is ``min(32, cpu_count + 4)`` which is too generous for
    # small memory-constrained worker pods.
    MAX_WORKER_THREADS: int = 8

    # Per-instance task concurrency gate (SA-1530). 0 = unlimited (legacy) —
    # opt-in per worker type. When set, /api/v1/execute-async acquires a slot
    # BEFORE returning 202; at capacity it returns 429 WORKER_SATURATED with
    # a Retry-After header so the executor REQUEUEs (SA-1531) instead of
    # failing the task. saturation = active/max is THE autoscale signal —
    # exposed on /ready, /api/v1/info and the worker_active_tasks metric.
    # Guidance: I/O-bound (async handlers) 32-64; sync handlers <= MAX_WORKER_THREADS;
    # CPU-bound ≈ cores.
    WORKER_MAX_CONCURRENT: int = 0
    WORKER_SATURATION_RETRY_AFTER_S: int = 2

    def get_tags_list(self) -> list[str]:
        if not self.WORKER_TAGS:
            return []
        return [t.strip() for t in self.WORKER_TAGS.split(",") if t.strip()]


settings = Settings()
