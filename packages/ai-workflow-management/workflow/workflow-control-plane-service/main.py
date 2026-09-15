import logging
import asyncio
import uvicorn
from contextlib import asynccontextmanager
from app.layer4_frameworks.config.app_config import settings
from bootstrap import build_app_container
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app

logger = logging.getLogger(__name__)


# --- Socket.IO server (created before bootstrap so it can be injected) ---
try:
    import socketio

    sio_kwargs = {
        "async_mode": "asgi",
        
        "cors_allowed_origins": "*",
        "ping_timeout": 60,
        "ping_interval": 25,
        "max_http_buffer_size": 1_000_000,
        "logger": False,
        "engineio_logger": False,
    }

    # Use Redis adapter for horizontal scaling if REDIS_URL is configured.
    # Falls back to in-memory (single-instance) if not set or connection fails.
    from app.layer4_frameworks.realtime import create_socketio_client_manager
    _mgr = create_socketio_client_manager(settings.REDIS_URL)
    if _mgr is not None:
        sio_kwargs["client_manager"] = _mgr

    sio_server = socketio.AsyncServer(**sio_kwargs)
except ImportError:
    sio_server = None
    logger.warning("python-socketio not installed — realtime push disabled")

# --- Socket.IO room join/leave handlers ---
# Clients emit these events to subscribe to realtime updates for a run or workflow.
# Room format matches broadcasting_publisher: "run:{run_id}", "workflow:{workflow_id}".
if sio_server is not None:
    @sio_server.on("connect")
    async def _on_connect(sid, environ):
        logger.info("Socket.IO client connected: %s", sid)

    @sio_server.on("disconnect")
    async def _on_disconnect(sid):
        logger.info("Socket.IO client disconnected: %s", sid)

    @sio_server.on("join:run")
    async def _on_join_run(sid, data):
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if not run_id:
            return
        room = f"run:{run_id}"
        await sio_server.enter_room(sid, room)
        logger.debug("Client %s joined room %s", sid, room)

    @sio_server.on("leave:run")
    async def _on_leave_run(sid, data):
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if not run_id:
            return
        room = f"run:{run_id}"
        await sio_server.leave_room(sid, room)
        logger.debug("Client %s left room %s", sid, room)

    @sio_server.on("join:workflow")
    async def _on_join_workflow(sid, data):
        workflow_id = data.get("workflow_id") if isinstance(data, dict) else None
        if not workflow_id:
            return
        room = f"workflow:{workflow_id}"
        await sio_server.enter_room(sid, room)
        logger.debug("Client %s joined room %s", sid, room)

    @sio_server.on("leave:workflow")
    async def _on_leave_workflow(sid, data):
        workflow_id = data.get("workflow_id") if isinstance(data, dict) else None
        if not workflow_id:
            return
        room = f"workflow:{workflow_id}"
        await sio_server.leave_room(sid, room)
        logger.debug("Client %s left room %s", sid, room)

container = build_app_container(sio_server=sio_server)


_grpc_server = None


@asynccontextmanager
async def lifespan(app):
    global _grpc_server
    # --- Startup ---

    # Start async background tasks on the message broker (e.g. Event Mesh flusher)
    infra = container.get("_infra")
    if infra is not None:
        await infra.start_async()

    # Pass the main event loop to broker clients so background consumer
    # threads can schedule async handlers without creating throwaway loops.
    _main_loop = asyncio.get_running_loop()
    if infra is not None:
        broker = getattr(infra, "broker", None)
        if broker is not None and hasattr(broker, "set_event_loop"):
            broker.set_event_loop(_main_loop)

    # Async HANA executor (Part 2 of the async-I/O fix).
    # When ``ASYNC_HANA_ENABLED`` is true, install a dedicated
    # ``ThreadPoolExecutor`` on the HANA connection manager so the
    # ``aexecute_*`` wrappers offload sync ``hdbcli`` calls off the
    # event loop. Sized to match the connection pool so a connection
    # is never starved waiting for a thread. When disabled, sync
    # ``execute_*`` paths keep working as before.
    _hana_executor = None
    db = getattr(infra, "db", None) if infra is not None else None
    monitor = getattr(infra, "monitor", None) if infra is not None else None
    # Pool + executor metrics (Task #15). Always wire the monitor onto
    # the HANA connection manager when available so
    # ``hana_pool_acquire_wait_seconds`` /
    # ``hana_pool_acquire_timeout_total`` fire regardless of whether
    # the async wrappers are enabled. The monitor reference is a
    # no-op when ``None`` is wired (mock infra mode).
    if db is not None and monitor is not None and hasattr(db, "set_monitor"):
        db.set_monitor(monitor)
        logger.info("HANA connection manager monitor wired")
    if monitor is not None:
        # Per-use-case CAS retry counters from the save_run_with_cas_retry
        # helper. Module-level monitor; safe to overwrite on hot-reload.
        from app.layer2_application import run_save_retry as _cas_retry
        _cas_retry.set_monitor(monitor)
        logger.info("CAS retry monitor wired")
    if settings.ASYNC_HANA_ENABLED:
        from concurrent.futures import ThreadPoolExecutor
        if db is not None and hasattr(db, "set_executor"):
            _hana_executor = ThreadPoolExecutor(
                max_workers=settings.HANA_EXECUTOR_THREADS,
                thread_name_prefix="hana-",
            )
            db.set_executor(_hana_executor)
            logger.info(
                "Async HANA executor installed (threads=%d, pool=%d+%d)",
                settings.HANA_EXECUTOR_THREADS,
                settings.HANA_POOL_SIZE,
                settings.HANA_MAX_OVERFLOW,
            )
        else:
            logger.warning(
                "ASYNC_HANA_ENABLED=true but infra.db has no "
                "set_executor — async wrappers will fall back to the "
                "loop's default executor",
            )

    # Event-loop lag sampler (SA-2257 P0). Started as early as possible so it
    # covers the rest of startup too — bootstrap itself does synchronous work
    # on this loop. This service's ceiling is loop time, and until this landed
    # nothing measured it: a frozen loop looked identical to a healthy one in
    # every other metric.
    lag_monitor = None
    lag_task = None
    if settings.EVENT_LOOP_LAG_ENABLED:
        from app.layer4_frameworks.monitoring.event_loop_lag import (
            EventLoopLagMonitor,
        )
        lag_monitor = EventLoopLagMonitor(
            monitor,
            interval_s=settings.EVENT_LOOP_LAG_INTERVAL_S,
            report_interval_s=settings.EVENT_LOOP_LAG_REPORT_INTERVAL_S,
            warn_threshold_s=settings.EVENT_LOOP_LAG_WARN_THRESHOLD_S,
        )
        container["_event_loop_lag_monitor"] = lag_monitor
        lag_task = asyncio.create_task(lag_monitor.run_forever())

    # Cache expiry sweep (SA-2368). Every named BoundedTTLCache can drop its
    # expired entries, and until now nothing ever asked it to: expiry only ran
    # lazily inside ``get()``, which never revisits a key nobody reads again.
    # For ``run_credentials`` that meant a decrypted vault secret stayed
    # resident until 512 later runs pushed it out — measured 3.9 days on
    # tenant-1 against its own 300s TTL. Started here rather than in bootstrap
    # because it needs the running loop.
    cache_janitor = None
    cache_janitor_task = None
    if settings.CACHE_EXPIRY_SWEEP_ENABLED:
        from app.layer4_frameworks.caching.cache_expiry_janitor import (
            CacheExpiryJanitor,
        )
        cache_janitor = CacheExpiryJanitor(
            monitor,
            interval_s=settings.CACHE_EXPIRY_SWEEP_INTERVAL_S,
        )
        container["_cache_expiry_janitor"] = cache_janitor
        cache_janitor_task = asyncio.create_task(
            cache_janitor.run_forever(), name="cache-expiry-janitor",
        )

    # malloc_trim janitor. ``resource_collector.malloc_trim`` shipped in #1962
    # and nothing called it. Measured on tenant-1 2026-08-21: the arena held the
    # identical 976.6 MiB for 7 hours while ``in_use`` sat at 42 MiB and the pod
    # ran 2 nodes; 934 MiB of that was free in 1 459 chunks, a 655 KiB mean
    # against a 4 KiB page, so by the rule in ``read_malloc_info`` it was 164x
    # over the releasable threshold. The only mechanism that had ever returned
    # memory on that pod was the process dying. Started here rather than in
    # bootstrap because it needs the running loop.
    trim_janitor = None
    trim_janitor_task = None
    if settings.MALLOC_TRIM_ENABLED:
        from app.layer4_frameworks.monitoring.malloc_trim_janitor import (
            MallocTrimJanitor,
        )
        trim_janitor = MallocTrimJanitor(
            monitor,
            interval_s=settings.MALLOC_TRIM_INTERVAL_S,
            min_free_bytes=settings.MALLOC_TRIM_MIN_FREE_BYTES,
            max_duration_ms=settings.MALLOC_TRIM_MAX_DURATION_MS,
            pad_bytes=settings.MALLOC_TRIM_PAD_BYTES,
        )
        container["_malloc_trim_janitor"] = trim_janitor
        trim_janitor_task = asyncio.create_task(
            trim_janitor.run_forever(), name="malloc-trim-janitor",
        )

    # Pre-create the centralized workflow event queue. ``EnrichingEventPublisher``
    # publishes to it on every event via ``publish_to_queue`` (Event Mesh
    # branch); unlike topics that ``ensure_topic`` creates implicitly via a
    # consumer's startup, this queue has no in-process consumer here, so
    # nothing else provisions it. Without this hook the first publish 404s
    # with "Queue does not exist" and every subsequent flush retries.
    if infra is not None:
        broker = getattr(infra, "broker", None)
        ensure_queue = getattr(broker, "create_queue", None)
        queue_exists = getattr(broker, "queue_exists", None)
        wf_event_queue = settings.WORKFLOW_EVENT_TOPIC
        if callable(ensure_queue) and callable(queue_exists) and wf_event_queue:
            try:
                if not queue_exists(wf_event_queue):
                    ensure_queue(wf_event_queue)
                    logger.info("Created workflow event queue: %s", wf_event_queue)
            except Exception:
                # Don't block startup on broker provisioning — publishes will
                # surface their own errors and be retried by the flusher.
                logger.exception(
                    "Failed to ensure workflow event queue %s exists",
                    wf_event_queue,
                )

    # Child workflow timeout sweeper (temporary)
    sweep_uc = container.get("sweep_timed_out_child_runs")
    sweep_task = None
    if sweep_uc is not None:
        async def _child_timeout_sweeper():
            while True:
                await asyncio.sleep(settings.CHILD_TIMEOUT_SWEEP_INTERVAL_SECONDS)
                try:
                    await sweep_uc.execute()
                except Exception:
                    logger.exception("Child timeout sweeper error")
        sweep_task = asyncio.create_task(_child_timeout_sweeper())
        logger.info("Child workflow timeout sweeper started (interval=%ds)", settings.CHILD_TIMEOUT_SWEEP_INTERVAL_SECONDS)

    # Unservable dispatched-task liveness sweeper (Signal A, opt-in). Fails a
    # dispatched TASK whose worker_type has zero live workers, after a grace.
    # Default OFF (WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED) — a live worker of
    # the type keeps its tasks safe regardless of duration.
    liveness_uc = container.get("sweep_unservable_dispatched_tasks")
    liveness_task = None
    if liveness_uc is not None and settings.WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED:
        async def _dispatch_liveness_sweeper():
            while True:
                await asyncio.sleep(settings.WORKER_DISPATCH_LIVENESS_SWEEP_INTERVAL_SECONDS)
                try:
                    await liveness_uc.execute()
                except Exception:
                    logger.exception("Dispatched-task liveness sweeper error")
        liveness_task = asyncio.create_task(_dispatch_liveness_sweeper())
        logger.info(
            "Dispatched-task liveness sweeper started (interval=%ds, grace=%ds)",
            settings.WORKER_DISPATCH_LIVENESS_SWEEP_INTERVAL_SECONDS,
            settings.WORKER_DISPATCH_LIVENESS_GRACE_SECONDS,
        )

    # Worker-registry observability mirror (SA-2050, R3, opt-in). Snapshot-
    # reconciles the executor's in-memory registry into AIW_WORKER_REGISTRY_MIRROR
    # for DBeaver/SQL. Read-only for humans — never a dispatch source. Default OFF
    # (WORKER_MIRROR_ENABLED); enable on EXACTLY ONE control-plane instance.
    mirror_uc = container.get("worker_mirror_reconcile")
    mirror_task = None
    if mirror_uc is not None and settings.WORKER_MIRROR_ENABLED:
        async def _worker_mirror_loop():
            while True:
                await asyncio.sleep(settings.WORKER_MIRROR_INTERVAL_SECONDS)
                try:
                    await mirror_uc.execute()
                except Exception:
                    logger.exception("Worker registry mirror reconcile error")
        mirror_task = asyncio.create_task(_worker_mirror_loop())
        logger.info(
            "Worker registry mirror started (interval=%ds)",
            settings.WORKER_MIRROR_INTERVAL_SECONDS,
        )

    # Worker replica-health realtime feed (SA-2056, R9, opt-in). Diffs the R6
    # /workers/replicas aggregate every interval and publishes per-worker_type
    # deltas to the realtime room for worker health. Default OFF
    # (WORKER_HEALTH_FEED_ENABLED); enable on EXACTLY ONE control-plane instance.
    health_feed_uc = container.get("worker_health_feed")
    health_feed_task = None
    if health_feed_uc is not None and settings.WORKER_HEALTH_FEED_ENABLED:
        async def _worker_health_feed_loop():
            while True:
                await asyncio.sleep(settings.WORKER_HEALTH_FEED_INTERVAL_SECONDS)
                try:
                    await health_feed_uc.execute()
                except Exception:
                    logger.exception("Worker health feed error")
        health_feed_task = asyncio.create_task(_worker_health_feed_loop())
        logger.info(
            "Worker health feed started (interval=%ds)",
            settings.WORKER_HEALTH_FEED_INTERVAL_SECONDS,
        )

    # Schedule (crontab) trigger scheduler. Present only when
    # WORKFLOW_SCHEDULER_ENABLED (bootstrap gates the container entry);
    # enable on exactly one instance. See
    # docs/design/schedule-trigger-scheduler.md.
    sched_uc = container.get("fire_due_schedules")
    sched_task = None
    if sched_uc is not None:
        async def _schedule_scheduler():
            while True:
                await asyncio.sleep(settings.SCHEDULER_TICK_SECONDS)
                try:
                    await sched_uc.execute()
                except Exception:
                    logger.exception("Schedule scheduler error")
        sched_task = asyncio.create_task(_schedule_scheduler())
        logger.info("Schedule trigger scheduler started (tick=%ds)", settings.SCHEDULER_TICK_SECONDS)

    # Gateway OpenAPI spec sync poller. Re-fetches each gateway app's spec_url and
    # reconciles its functions. Not idempotent across instances — enable on exactly
    # one. The on-demand POST /gateway/apps/{name}/sync works regardless.
    openapi_sync_uc = container.get("sync_gateway_app_openapi")
    openapi_sync_task = None
    if openapi_sync_uc is not None and settings.GATEWAY_OPENAPI_SYNC_ENABLED:
        async def _openapi_sync_sweeper():
            while True:
                await asyncio.sleep(settings.GATEWAY_OPENAPI_SYNC_INTERVAL_SECONDS)
                try:
                    await openapi_sync_uc.execute_all()
                except Exception:
                    logger.exception("Gateway OpenAPI sync sweeper error")
        openapi_sync_task = asyncio.create_task(_openapi_sync_sweeper())
        logger.info(
            "Gateway OpenAPI sync sweeper started (interval=%ds)",
            settings.GATEWAY_OPENAPI_SYNC_INTERVAL_SECONDS,
        )

    # Orchestration background workers. With ORCHESTRATION_WORKER_COUNT=1
    # this spawns a single coroutine — identical to the historical
    # behaviour. With N > 1 (default 4 for a 1 GB pod), N coroutines drain
    # the queue concurrently and the engine's per-run asyncio.Lock keeps
    # same-run actions serial.
    orch_workers = container.get("_orchestration_workers") or []
    if not orch_workers:
        fallback = container.get("_orchestration_worker")
        if fallback is not None:
            orch_workers = [fallback]
    orch_tasks: list[asyncio.Task] = []
    if orch_workers:
        for worker in orch_workers:
            orch_tasks.append(asyncio.create_task(worker.run_forever()))
        logger.info(
            "Orchestration background workers started (count=%d)",
            len(orch_workers),
        )
        # For Kafka queue, start the consumer subscription (shared across workers)
        orch_queue = container.get("_orchestration_queue")
        if hasattr(orch_queue, "start_consumer"):
            orch_queue.start_consumer()

    # Phase M.3.3: per-instance response topic listener — only present
    # in broker mode. Picks up EditNodeDataAction responses published by
    # peer instances and resolves the local ResponseRegistry futures.
    response_listener = container.get("_response_topic_listener")
    if response_listener is not None:
        response_listener.start()
        logger.info("Orchestration response topic listener started (instance_id=%s)",
                    container.get("_instance_id"))

    # Push Gateway realtime consumer (broker topic → Push Gateway REST).
    # This is the only realtime sink: the Socket.IO consumer was removed once
    # every environment moved to the Push Gateway.
    pg_consumer = container.get("_push_gateway_consumer")
    pg_consumer_task = None
    if pg_consumer is not None:
        pg_consumer.start()
        pg_consumer_task = asyncio.create_task(pg_consumer.run_forever())
        logger.info("Push Gateway realtime consumer started")

    # catalog.changed consumer (broker topic → NodeCatalogCache invalidation)
    catalog_consumer = container.get("_catalog_changed_consumer")
    if catalog_consumer is not None:
        catalog_consumer.start()
        logger.info("catalog.changed consumer started")

    # Worker results consumer (executor's ``workflow.results`` topic →
    # complete_task / fail_task). Present only in broker mode with
    # TRANSPORT_MODE=event_mesh, where the executor reports results over the
    # broker instead of the synchronous HTTP/gRPC result-reporter. Without
    # this, completions published by the executor are never consumed and runs
    # hang in RUNNING.
    worker_results_consumer = container.get("_worker_results_consumer")
    if worker_results_consumer is not None:
        worker_results_consumer.start()
        logger.info("Worker results consumer started (topic=workflow.results)")

    # Phase 3 Sub 3.2 — outbox dispatcher background loop. Idles with
    # no producer wired (Subs 3.3 / 3.4 add producers).
    outbox_dispatcher = container.get("_outbox_dispatcher")
    outbox_task = None
    if outbox_dispatcher is not None:
        outbox_task = asyncio.create_task(outbox_dispatcher.run_forever())
        logger.info("Outbox dispatcher started")

    # Durable timers (SA-1371) — leader-gated timer dispatcher background
    # loop. Present only when TIMER_DISPATCHER_ENABLED (single-owner lease is
    # always-leader in v1, so enable on exactly one pod). See SA-1371.
    timer_dispatcher = container.get("_timer_dispatcher")
    timer_task = None
    if timer_dispatcher is not None and container.get("_timer_dispatcher_enabled"):
        timer_task = asyncio.create_task(timer_dispatcher.run_forever())
        logger.info(
            "Timer dispatcher started (poll=%ss)",
            settings.TIMER_DISPATCHER_POLL_INTERVAL_S,
        )

    # Durable timers (SA-1371 S4) — TimerFiredConsumer subscribes to
    # ``timer.fired`` and resumes a parked WAIT node when its timer fires.
    # ``None`` (broker absent or TIMER_RESUME_CONSUMER_ENABLED off) → no-op.
    timer_fired_consumer = container.get("_timer_fired_consumer")
    if timer_fired_consumer is not None:
        timer_fired_consumer.start()
        logger.info("Timer fired consumer started (topic=timer.fired)")

    # Event triggers (SA-2059) — EventTriggerConsumer subscribes to
    # ``workflow.triggers`` and starts a run for every workflow whose Event
    # trigger matches the topic. ``None`` (broker absent or
    # EVENT_TRIGGER_CONSUMER_ENABLED off) → no-op.
    event_trigger_consumer = container.get("_event_trigger_consumer")
    if event_trigger_consumer is not None:
        event_trigger_consumer.start()
        logger.info("Event trigger consumer started (topic=workflow.triggers)")

    # Smartsheet event trigger (smartsheet-event-trigger) — poller drains the
    # org-wide Event Reporting API and republishes onto workflow.triggers.
    # ``None`` (broker absent, flag off, or no token) → no-op.
    smartsheet_poller = container.get("_smartsheet_poller")
    smartsheet_task = None
    if smartsheet_poller is not None:
        _ss_interval = float(getattr(settings, "SMARTSHEET_POLL_INTERVAL_SECONDS", 300))
        smartsheet_task = asyncio.create_task(smartsheet_poller.run_forever(_ss_interval))
        logger.info("Smartsheet poller started (interval=%ss)", _ss_interval)

    # Phase 3 Sub 3.5 — ExecutorConsumer. ``start()`` is a no-op when
    # ``EXECUTOR_MODE=in_process`` (default), so this block is safe to
    # run unconditionally.
    executor_consumer = container.get("_executor_consumer")
    executor_task = None
    if executor_consumer is not None:
        executor_consumer.start()
        if executor_consumer.is_active:
            executor_task = asyncio.create_task(
                executor_consumer.run_forever(),
            )
            logger.info(
                "ExecutorConsumer started (mode=%s)", executor_consumer.mode,
            )

    # SA-1526 — the gRPC server lifecycle is decoupled from TRANSPORT_MODE.
    # The Docker image EXPOSEs :50051, and CF's default port health check
    # probes EVERY exposed port — with the server gated on TRANSPORT_MODE=grpc,
    # flipping to event_mesh crash-looped the pod ("Instance never healthy:
    # dial tcp :50051"). Keeping the server up in every mode (flag-gated,
    # default on) also keeps existing external gRPC callers working while
    # task dispatch moves to the Event Mesh.
    if settings.GRPC_SERVER_ENABLED or settings.TRANSPORT_MODE.lower() == "grpc":
        try:
            from app.layer3_adapters.controllers.grpc.server import create_grpc_server
            _grpc_server = await create_grpc_server(
                container, port=settings.GRPC_PORT, max_workers=settings.GRPC_MAX_WORKERS,
            )
            await _grpc_server.start()
            logger.info(
                "CP gRPC server started on port %s (transport_mode=%s)",
                settings.GRPC_PORT, settings.TRANSPORT_MODE,
            )
        except Exception as exc:
            logger.error("Failed to start gRPC server: %s", exc)
    yield
    # --- Shutdown ---
    if lag_task is not None:
        # stop() first so the final window's peak is reported rather than
        # cancelled away — the last window before a shutdown is often the
        # interesting one.
        lag_monitor.stop()
        lag_task.cancel()
    if cache_janitor_task is not None:
        # Same order and the same reason: stop() lets the loop log its totals
        # before cancellation takes the frame away.
        cache_janitor.stop()
        cache_janitor_task.cancel()
    if trim_janitor_task is not None:
        # Same order again. Its totals — trims, MiB released, back-off state —
        # are the only place the aggregate shows up if the pod is going down
        # before the next scrape.
        trim_janitor.stop()
        trim_janitor_task.cancel()
    if pg_consumer_task is not None:
        pg_consumer_task.cancel()
        await pg_consumer.close()
    for orch_task in orch_tasks:
        orch_task.cancel()
    if sweep_task is not None:
        sweep_task.cancel()
    if liveness_task is not None:
        liveness_task.cancel()
    if mirror_task is not None:
        mirror_task.cancel()
    if health_feed_task is not None:
        health_feed_task.cancel()
    if sched_task is not None:
        sched_task.cancel()
    if openapi_sync_task is not None:
        openapi_sync_task.cancel()
    if outbox_task is not None:
        outbox_dispatcher.stop()
        outbox_task.cancel()
    if timer_task is not None:
        timer_dispatcher.stop()
        timer_task.cancel()
    if executor_task is not None:
        executor_consumer.stop()
        executor_task.cancel()
    if smartsheet_task is not None:
        smartsheet_task.cancel()
    # SA-2087 (R12): release this instance's per-instance response queue
    # BEFORE the broker is closed below — after ``infra.close()`` the
    # management client is gone and the queue would be orphaned. Never
    # raises; see ResponseTopicListener.stop().
    if response_listener is not None:
        response_listener.stop()
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5)
        logger.info("CP gRPC server stopped")
    infra = container.get("_infra")
    if infra is not None:
        # Drain broker send buffer before closing
        await infra.stop_async(drain_timeout_s=10.0)
        infra.close()
    if _hana_executor is not None:
        db = getattr(infra, "db", None) if infra is not None else None
        if db is not None and hasattr(db, "set_executor"):
            db.set_executor(None)
        _hana_executor.shutdown(wait=False)
        logger.info("Async HANA executor shut down")


if __name__ == "__main__":
    mode = settings.APP_MODE
    if mode == "REST":
        logger.info("Mode: REST API")
        fastapi_app = create_fastapi_app(container, lifespan=lifespan)

        if sio_server is not None:
            app = socketio.ASGIApp(sio_server, other_asgi_app=fastapi_app)
        else:
            app = fastapi_app

        # Keep-alive must outlast the CF gorouter's backend connection reuse
        # window; uvicorn's 5s default lets the router reuse a socket uvicorn
        # has just closed, surfacing as 502 endpoint_failure (EOF).
        uvicorn.run(app, host="0.0.0.0", port=settings.SERVICE_PORT, timeout_keep_alive=75)
