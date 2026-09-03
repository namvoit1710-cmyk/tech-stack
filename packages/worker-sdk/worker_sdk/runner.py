import asyncio
import logging
from typing import Any, Optional

import uvicorn

from worker_sdk.layer4_frameworks.config.app_config import Settings, settings
from worker_sdk.bootstrap import build_app_container
from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app
from worker_sdk.layer3_adapters.controllers.worker_headless import run_headless_worker
from worker_sdk.layer3_adapters.controllers.worker_pull import run_pull_worker

_log = logging.getLogger("WorkerSDK")


def _merge_app_settings_into_singleton(app_settings: Settings) -> None:
    """Copy fields from a worker-supplied Settings instance onto the SDK singleton.

    The SDK singleton is imported with a cached local name in several modules
    (``bootstrap``, ``worker_server``, ``worker_headless``, ``standard_logger``,
    ``http_worker_registry``, ``get_worker_info_usecase``). Rebinding the
    attribute on the config module would not update those cached names, so
    instead we mutate the existing instance in place. Pydantic v2 BaseSettings
    allows attribute assignment by default, and downstream consumers only read
    the attributes — they do not inspect ``model_fields_set``.

    After this call, every ``settings.X`` access across the SDK sees the
    effective value the worker's ``Settings()`` constructor produced, which
    already reflects **env/.env > worker subclass default > base SDK default**
    via Pydantic's own precedence at instantiation time.

    Only fields the SDK singleton itself declares are merged. A worker may
    subclass ``Settings`` and add its own fields (e.g. the gateway-worker's
    ``CONTROL_PLANE_URL``); those are read directly off the worker's
    ``app_settings`` in its ``main.py`` and are unknown to the singleton, so
    assigning them here would raise ``"Settings" object has no field``.
    """
    for field_name in type(settings).model_fields:
        setattr(settings, field_name, getattr(app_settings, field_name))


def run_worker(
    features_path: Optional[str] = None,
    base_module: Optional[str] = None,
    extra_dependencies: Optional[dict[str, Any]] = None,
    functions: Optional[list] = None,
    node_types: Optional[list] = None,
    app_settings: Optional[Settings] = None,
) -> None:
    """Convenience entry-point for running a worker in SERVER or HEADLESS mode.

    Args:
        functions: Optional list of WorkerFunction instances to register.
        node_types: Optional list of NodeTypeDefinition instances for multi-type workers.
            When provided, each definition registers as a separate worker_type with the
            executor and appears as its own node in the workflow builder.
        app_settings: Optional ``Settings`` instance — typically the singleton from a
            worker-specific ``Settings`` subclass (see each concrete worker's
            ``app/layer4_frameworks/config.py``). When provided, its field values are
            merged into the SDK's singleton **before** any service is constructed, so
            the effective precedence becomes **env/.env > worker subclass default >
            base SDK default**. The precedence is produced by Pydantic itself at
            ``Settings()`` instantiation time; this merge just hands the result to the
            SDK's shared singleton.
    """
    if app_settings is not None:
        _merge_app_settings_into_singleton(app_settings)

    container = build_app_container(
        features_path=features_path,
        base_module=base_module,
        extra_dependencies=extra_dependencies,
        functions=functions,
        node_types=node_types,
    )

    mode = settings.APP_MODE

    if mode == "SERVER":
        _log.debug(
            "Starting SERVER mode: host=%s, port=%s, proxy_url=%s",
            settings.SERVER_HOST, settings.SERVER_PORT,
            settings.PROXY_URL,
        )
        print(f"Mode: SERVER on {settings.SERVER_HOST}:{settings.SERVER_PORT}")
        app = create_worker_app(container)
        uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)

    elif mode == "HEADLESS":
        _log.debug("Starting HEADLESS mode")
        print("Mode: HEADLESS")
        asyncio.run(run_headless_worker(container))

    elif mode == "PULL":
        _log.debug("Starting PULL mode (lease-based delivery)")
        print(f"Mode: PULL (leasing '{settings.WORKER_TYPE}' from {settings.REGISTRY_URL})")
        asyncio.run(run_pull_worker(container))

    else:
        raise ValueError(f"Unknown APP_MODE: {mode}. Use SERVER, HEADLESS, or PULL.")
