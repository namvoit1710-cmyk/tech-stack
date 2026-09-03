import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from worker_sdk.layer4_frameworks.config.app_config import settings
from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus
from worker_sdk.layer1_domain.value_objects.port import default_task_ports
from worker_sdk.layer3_adapters.controllers.restful.v1.routes import create_router
from typing import Any

_log = logging.getLogger("WorkerSDK")


_ALLOW_EMPTY_WORKER_TYPE_ENV = "SDK_ALLOW_EMPTY_WORKER_TYPE"


def _assert_worker_type(worker_type: str, context: str) -> None:
    """Fail fast if worker_type is blank. Emergency override via env var."""
    import os
    if worker_type and worker_type.strip():
        return
    if os.environ.get(_ALLOW_EMPTY_WORKER_TYPE_ENV, "").lower() in ("true", "1", "yes"):
        _log.warning("WORKER_TYPE is blank (%s) but %s=true — allowing registration",
                     context, _ALLOW_EMPTY_WORKER_TYPE_ENV)
        return
    raise RuntimeError(
        f"WORKER_TYPE is required and cannot be blank ({context}). "
        f"Set WORKER_TYPE env var or construct NodeTypeDefinition with a non-empty worker_type."
    )


def _build_registration_for_node_type(nt: Any, endpoint: str) -> WorkerRegistration:
    """Build a WorkerRegistration from a NodeTypeDefinition."""
    func_defs = [f.to_definition() for f in (nt.functions or [])]
    return WorkerRegistration(
        worker_type=nt.worker_type,
        version=nt.version or settings.WORKER_VERSION,
        spec_version=getattr(nt, "spec_version", "1.0.0") or "1.0.0",
        endpoint=endpoint,
        sdk_version=settings.SDK_VERSION,
        input_schema=nt.input_schema,
        output_schema=nt.output_schema,
        name=nt.name or nt.worker_type,
        description=nt.description,
        node_class=nt.node_class,
        kind=getattr(nt, "kind", NodeKind.ACTION),
        icon=nt.icon,
        color=nt.color,
        tags=nt.tags,
        capabilities=nt.capabilities or [{"domain": nt.worker_type, "action": "execute"}],
        ports=nt.ports or default_task_ports(),
        functions=func_defs,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> Any:
    """Manages worker registration, heartbeat loop, and deregistration."""
    registry = app.state.dependencies.get("worker_registry")
    node_type_registry = app.state.dependencies.get("node_type_registry")
    # List of (worker_id, registration) tuples for multi-type; single-type uses first entry only
    registrations: list[tuple[str, WorkerRegistration]] = []
    heartbeat_task = None

    # Bound the default executor thread pool used by ``asyncio.to_thread``
    # (which the SDK uses to run sync node-type handlers off the event
    # loop). Python's default is ``min(32, cpu_count + 4)``, which is too
    # generous for small worker pods — 32 concurrent sync handlers each
    # holding ~10 MB of working memory + thread stack would OOM a 256 MB
    # pod. ``MAX_WORKER_THREADS`` defaults to 8 for 256 MB; raise it for
    # larger pods (see config docstring for sizing math).
    try:
        max_threads = max(1, int(getattr(settings, "MAX_WORKER_THREADS", 8)))
        from concurrent.futures import ThreadPoolExecutor
        executor = ThreadPoolExecutor(
            max_workers=max_threads,
            thread_name_prefix="worker-sdk",
        )
        asyncio.get_running_loop().set_default_executor(executor)
        _log.debug(
            "Default executor sized to %d threads (MAX_WORKER_THREADS)",
            max_threads,
        )
    except Exception:
        # Don't block worker startup on executor reconfiguration; fall
        # back to Python's default pool.
        _log.exception("Failed to size default executor; using Python default")

    # --- Startup ---
    # B-B (SA-2055): defined HERE, not inside the try below. This is not
    # cosmetic — if register() throws, execution never reaches a definition
    # placed after it, so create_task(_heartbeat_loop()) would raise NameError
    # and B-B would still be live, just with a different corpse.
    # `nonlocal registrations` still binds (we are still nested in _lifespan);
    # `registry` is captured by closure from above.
    async def _heartbeat_loop() -> None:
        nonlocal registrations
        while True:
            await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
            updated: list[tuple[str, WorkerRegistration]] = []
            for wid, reg in registrations:
                try:
                    if not wid:
                        # B-B (SA-2055): this type's boot register failed (the
                        # executor was down). THIS loop is the retry path — a
                        # worker that boots against a dead executor recovers
                        # within one interval instead of never.
                        #
                        # The spec says "the loop's re_register path IS the
                        # retry". It is not, on its own: re_register only ever
                        # arrives on a heartbeat RESPONSE, and you cannot
                        # heartbeat a wid you never got. This branch is the
                        # missing half.
                        wid = await registry.register(reg)
                        print(f"Registered '{reg.worker_type}' as {wid} (recovered)")
                    else:
                        result = await registry.heartbeat(wid, WorkerStatus.HEALTHY)
                        if isinstance(result, dict) and result.get("re_register"):
                            print(f"Executor signaled re-registration for {reg.worker_type}, re-registering...")
                            wid = await registry.register(reg)
                            print(f"Re-registered {reg.worker_type} as {wid}")
                except Exception as exc:
                    # A blank wid means the except fired on the register-retry
                    # branch, not a heartbeat — label it honestly (a boot-fail
                    # worker never had a wid to heartbeat).
                    what = "Heartbeat" if wid else "Re-registration"
                    print(f"{what} failed for {reg.worker_type}: {exc}")
                updated.append((wid, reg))
            registrations = updated

    if registry is not None:
        # Build every registration first — construction cannot reach the network,
        # so a failure here is a CONFIG error (blank WORKER_TYPE), not a dead
        # executor, and must not be conflated with one.
        pending: list[WorkerRegistration] = []
        try:
            endpoint = settings.PROXY_URL

            if node_type_registry:
                # Multi-type mode: each node type registers as its own node.
                for wt, nt in node_type_registry.items():
                    _assert_worker_type(nt.worker_type, f"node_type_registry[{wt}]")
                    _log.debug("Building registration for node type: %s", wt)
                    pending.append(_build_registration_for_node_type(nt, endpoint))
            else:
                # Single-type mode: existing behavior
                _assert_worker_type(settings.WORKER_TYPE, "settings.WORKER_TYPE")
                _log.debug("Building registration: endpoint=%s, type=%s, version=%s",
                            endpoint, settings.WORKER_TYPE, settings.WORKER_VERSION)
                func_defs = []
                func_reg = app.state.dependencies.get("function_registry")
                if func_reg:
                    func_defs = func_reg.list_definitions()

                pending.append(WorkerRegistration(
                    worker_type=settings.WORKER_TYPE,
                    version=settings.WORKER_VERSION,
                    spec_version=getattr(settings, "WORKER_SPEC_VERSION", "1.0.0") or "1.0.0",
                    endpoint=endpoint,
                    sdk_version=settings.SDK_VERSION,
                    input_schema=app.state.dependencies.get("input_schema"),
                    output_schema=app.state.dependencies.get("output_schema"),
                    name=settings.WORKER_NAME or settings.WORKER_TYPE,
                    description=settings.WORKER_DESCRIPTION,
                    node_class=settings.WORKER_NODE_CLASS,
                    icon=settings.WORKER_ICON,
                    color=settings.WORKER_COLOR,
                    tags=settings.get_tags_list(),
                    capabilities=app.state.dependencies.get("capabilities", [{"domain": settings.WORKER_TYPE, "action": "execute"}]),
                    ports=app.state.dependencies.get("ports") or default_task_ports(),
                    functions=func_defs,
                ))
        except Exception as exc:
            print(f"Registration failed (could not build registration): {exc}")

        # B-B (SA-2055): register each type INDEPENDENTLY. Previously all
        # register calls shared one try — the first failure skipped the rest
        # entirely. A failure now parks a BLANK wid, which _heartbeat_loop
        # reads as "still needs registering" and retries.
        for reg in pending:
            try:
                wid = await registry.register(reg)
                print(f"Registered '{reg.worker_type}' as {wid}")
            except Exception as exc:
                wid = ""
                print(f"Registration failed for {reg.worker_type} (will retry): {exc}")
            registrations.append((wid, reg))

        # B-B (SA-2055): OUTSIDE the try. This line used to sit inside it, so a
        # register() throw meant the heartbeat task was never created and the
        # worker stayed silently unregistered forever — "continuing anyway" was
        # a lie; it continued as a zombie. The loop must start even when every
        # register failed: it is the retry path (Task 10).
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

    yield

    # --- Shutdown ---
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    if registry is not None:
        for wid, reg in registrations:
            # B-B (SA-2055): a blank wid means this type never successfully
            # registered — there is nothing to deregister.
            if not wid:
                continue
            try:
                await registry.deregister(wid)
                print(f"Deregistered worker {wid} ({reg.worker_type})")
            except Exception as exc:
                print(f"Deregistration failed for {reg.worker_type}: {exc}")

    if registry is not None and hasattr(registry, "close"):
        await registry.close()


def create_worker_app(container: dict) -> FastAPI:
    """Factory that builds the FastAPI application for SERVER mode."""
    app = FastAPI(title=settings.APP_NAME, lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Expose raw dependencies for the lifespan to use
    dependencies = container.setdefault("_dependencies", {})
    app.state.dependencies = dependencies

    # SA-1530 — per-instance concurrency gate, shared by the /execute-async
    # acceptance path (429 when saturated) and the observability endpoints.
    if "worker_concurrency" not in dependencies:
        from worker_sdk.layer2_application.services.worker_concurrency import (
            WorkerConcurrency,
        )
        dependencies["worker_concurrency"] = WorkerConcurrency(
            settings.WORKER_MAX_CONCURRENT,
        )
    concurrency = dependencies["worker_concurrency"]

    # --- Root-level infrastructure endpoints (not versioned) ---

    @app.get("/health")
    def health() -> Any:
        return {
            "status": "healthy",
            "worker_type": settings.WORKER_TYPE,
            "version": settings.WORKER_VERSION,
            "sdk_version": settings.SDK_VERSION,
        }

    @app.get("/ready")
    def ready() -> Any:
        # Saturated is BUSY, not broken: ready stays true at capacity so the
        # platform doesn't pull a healthy-but-full instance. The snapshot is
        # the autoscale/ops signal (SA-1530).
        return {"ready": True, "concurrency": concurrency.snapshot()}

    # --- Versioned business routes ---
    v1_router = create_router(container)
    app.include_router(v1_router, prefix="/api/v1")

    return app
