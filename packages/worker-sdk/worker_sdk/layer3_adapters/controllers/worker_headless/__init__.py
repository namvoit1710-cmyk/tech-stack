import asyncio
import logging

from worker_sdk.layer4_frameworks.config.app_config import settings
from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus
from worker_sdk.layer1_domain.value_objects.port import default_task_ports
from typing import Any

_log = logging.getLogger("WorkerSDK")


_ALLOW_EMPTY_WORKER_TYPE_ENV = "SDK_ALLOW_EMPTY_WORKER_TYPE"


def _assert_worker_type_headless(worker_type: str, context: str) -> None:
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


def _build_headless_registration_for_node_type(nt: Any) -> WorkerRegistration:
    """Build a WorkerRegistration from a NodeTypeDefinition for headless mode."""
    func_defs = [f.to_definition() for f in (nt.functions or [])]
    return WorkerRegistration(
        worker_type=nt.worker_type,
        version=nt.version or settings.WORKER_VERSION,
        spec_version=getattr(nt, "spec_version", "1.0.0") or "1.0.0",
        endpoint="headless",
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


async def run_headless_worker(container: dict) -> None:
    """
    Standalone async process for HEADLESS mode.
    No HTTP server - runs in-process or as a Kafka consumer, etc.
    """
    dependencies = container.get("_dependencies", {})
    registry = dependencies.get("worker_registry")
    node_type_registry = dependencies.get("node_type_registry")
    logger = dependencies.get("logger")
    # List of (worker_id, registration) tuples
    registrations: list[tuple[str, WorkerRegistration]] = []

    # Bound the asyncio default executor (see worker_server/__init__.py
    # for sizing math). HEADLESS workers may also dispatch sync handlers
    # via ``asyncio.to_thread`` for sync use cases, so the cap applies
    # here too.
    try:
        import asyncio as _asyncio
        from worker_sdk.layer4_frameworks.config.app_config import settings as _settings
        max_threads = max(1, int(getattr(_settings, "MAX_WORKER_THREADS", 8)))
        from concurrent.futures import ThreadPoolExecutor
        _executor = ThreadPoolExecutor(
            max_workers=max_threads,
            thread_name_prefix="worker-sdk-headless",
        )
        _asyncio.get_running_loop().set_default_executor(_executor)
    except Exception:
        _log.exception(
            "Failed to size default executor in HEADLESS; using Python default"
        )

    # --- Register ---
    if registry is not None:
        # Build first: construction cannot reach the network, so a failure here is
        # a CONFIG error (blank WORKER_TYPE), not a dead executor.
        pending: list[WorkerRegistration] = []
        try:
            if node_type_registry:
                # Multi-type mode
                for wt, nt in node_type_registry.items():
                    _assert_worker_type_headless(nt.worker_type, f"node_type_registry[{wt}]")
                    _log.debug("Building headless registration for node type: %s", wt)
                    pending.append(_build_headless_registration_for_node_type(nt))
            else:
                # Single-type mode: existing behavior
                _assert_worker_type_headless(settings.WORKER_TYPE, "settings.WORKER_TYPE")
                _log.debug("Building headless registration: type=%s, version=%s",
                            settings.WORKER_TYPE, settings.WORKER_VERSION)
                pending.append(WorkerRegistration(
                    worker_type=settings.WORKER_TYPE,
                    version=settings.WORKER_VERSION,
                    spec_version=getattr(settings, "WORKER_SPEC_VERSION", "1.0.0") or "1.0.0",
                    endpoint="headless",
                    sdk_version=settings.SDK_VERSION,
                    name=settings.WORKER_NAME or settings.WORKER_TYPE,
                    description=settings.WORKER_DESCRIPTION,
                    node_class=settings.WORKER_NODE_CLASS,
                    icon=settings.WORKER_ICON,
                    color=settings.WORKER_COLOR,
                    tags=settings.get_tags_list(),
                    capabilities=dependencies.get("capabilities", [{"domain": settings.WORKER_TYPE, "action": "execute"}]),
                    ports=dependencies.get("ports") or default_task_ports(),
                ))
        except Exception as exc:
            if logger:
                logger.error(f"Registration failed (could not build registration): {exc}")

        # B-B (SA-2055): register each type INDEPENDENTLY — one type's failure
        # must not skip the rest. A failure parks a BLANK wid, which the main
        # loop below reads as "still needs registering" and retries.
        for reg in pending:
            try:
                wid = await registry.register(reg)
                if logger:
                    logger.info(f"Headless '{reg.worker_type}' registered as {wid}")
            except Exception as exc:
                wid = ""
                if logger:
                    logger.error(f"Registration failed for {reg.worker_type} (will retry): {exc}")
            registrations.append((wid, reg))

    # --- Main loop ---
    try:
        if logger:
            logger.info("Headless worker running. Press Ctrl+C to stop.")
        while True:
            # Heartbeat all registrations
            if registry is not None and registrations:
                updated: list[tuple[str, WorkerRegistration]] = []
                for wid, reg in registrations:
                    try:
                        if not wid:
                            # B-B (SA-2055): this type's boot register failed (the
                            # executor was down). THIS loop is the retry path — a
                            # worker that boots against a dead executor recovers
                            # within one interval instead of never.
                            wid = await registry.register(reg)
                            if logger:
                                logger.info(f"Headless '{reg.worker_type}' registered as {wid} (recovered)")
                        else:
                            result = await registry.heartbeat(wid, WorkerStatus.HEALTHY)
                            if isinstance(result, dict) and result.get("re_register"):
                                if logger:
                                    logger.info(f"Executor signaled re-registration for {reg.worker_type}, re-registering...")
                                wid = await registry.register(reg)
                                if logger:
                                    logger.info(f"Re-registered {reg.worker_type} as {wid}")
                    except Exception as exc:
                        if logger:
                            # Blank wid => the except fired on the register-retry
                            # branch, not a heartbeat; label it for what it is.
                            what = "Heartbeat" if wid else "Re-registration"
                            logger.error(f"{what} failed for {reg.worker_type}: {exc}")
                    updated.append((wid, reg))
                registrations = updated

            await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        pass
    finally:
        # --- Deregister all ---
        if registry is not None:
            for wid, reg in registrations:
                # B-B (SA-2055): a blank wid never registered — nothing to undo.
                if not wid:
                    continue
                try:
                    await registry.deregister(wid)
                    if logger:
                        logger.info(f"Headless worker {wid} ({reg.worker_type}) deregistered")
                except Exception as exc:
                    if logger:
                        logger.error(f"Deregistration failed for {reg.worker_type}: {exc}")

        if registry is not None and hasattr(registry, "close"):
            await registry.close()
