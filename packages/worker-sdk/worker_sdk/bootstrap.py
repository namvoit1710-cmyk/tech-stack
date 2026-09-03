import importlib
import inspect
import logging
import os
import pkgutil
from typing import Any, Optional

from worker_sdk.layer4_frameworks.config.app_config import settings
from worker_sdk.layer4_frameworks.logger.standard_logger import StandardLogger
from worker_sdk.layer4_frameworks.providers.monitoring.prometheus_monitor import PrometheusMonitor
from worker_sdk.layer4_frameworks.providers.storage.local_storage import LocalStorage
from worker_sdk.layer4_frameworks.providers.data_io.local_input_reader import LocalInputReader
from worker_sdk.layer4_frameworks.providers.data_io.local_output_writer import LocalOutputWriter
from worker_sdk.layer4_frameworks.providers.registry.http_worker_registry import HttpWorkerRegistry
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import HttpFileRefResolver

_log = logging.getLogger("WorkerSDK")


def _to_pascal_case(snake_str: str) -> str:
    return "".join(word.capitalize() for word in snake_str.split("_"))


def scan_and_load_features(
    features_path: Optional[str] = None,
    base_module: Optional[str] = None,
) -> dict[str, Any]:
    """Discover feature packages under the given features directory."""
    registry: dict[str, Any] = {}
    if features_path is None:
        sdk_root = os.path.dirname(os.path.abspath(__file__))
        features_path = os.path.join(sdk_root, "layer2_application", "features")
    if base_module is None:
        base_module = "worker_sdk.layer2_application.features"

    print(f"Scanning features in: {features_path}")

    for _, module_name, is_pkg in pkgutil.iter_modules([features_path]):
        if is_pkg:
            try:
                full_module_name = f"{base_module}.{module_name}"
                module = importlib.import_module(full_module_name)
                expected_class_name = f"{_to_pascal_case(module_name)}UseCase"
                if hasattr(module, expected_class_name):
                    registry[f"{module_name}_usecase"] = getattr(module, expected_class_name)
                    print(f"   [+] Found Feature: {module_name}")
            except Exception as e:
                print(f"   [!] Error loading {module_name}: {e}")

    return registry


def build_app_container(
    features_path: Optional[str] = None,
    base_module: Optional[str] = None,
    extra_dependencies: Optional[dict[str, Any]] = None,
    functions: Optional[list] = None,
    node_types: Optional[list] = None,
) -> dict[str, Any]:
    """Composition Root: wire infrastructure into discovered use-cases."""
    print(f"Bootstrapping {settings.APP_NAME}...")

    # Debug: dump resolved configuration so operators can verify env source
    _log.debug("=== Worker Configuration ===")
    _log.debug("  APP_NAME          = %s", settings.APP_NAME)
    _log.debug("  APP_MODE          = %s", settings.APP_MODE)
    _log.debug("  WORKER_TYPE       = %s", settings.WORKER_TYPE)
    _log.debug("  WORKER_NAME       = %s", settings.WORKER_NAME)
    _log.debug("  WORKER_VERSION    = %s", settings.WORKER_VERSION)
    _log.debug("  SDK_VERSION       = %s", settings.SDK_VERSION)
    _log.debug("  WORKER_NODE_CLASS = %s", settings.WORKER_NODE_CLASS)
    _log.debug("  WORKER_ICON       = %s", settings.WORKER_ICON)
    _log.debug("  WORKER_COLOR      = %s", settings.WORKER_COLOR)
    _log.debug("  WORKER_TAGS       = %s", settings.WORKER_TAGS)
    _log.debug("  REGISTRY_URL      = %s", settings.REGISTRY_URL)
    _log.debug("  SERVER_HOST       = %s", settings.SERVER_HOST)
    _log.debug("  SERVER_PORT       = %s", settings.SERVER_PORT)
    _log.debug("  PROXY_URL         = %s", settings.PROXY_URL)
    _log.debug("  HEARTBEAT_INTERVAL= %s", settings.HEARTBEAT_INTERVAL_SECONDS)
    _log.debug("  LOG_LEVEL         = %s", settings.LOG_LEVEL)
    _log.debug("  DISABLE_HTTPX_LOG = %s", settings.DISABLE_HTTPX_LOG)
    _log.debug("  FILE_SERVICE_URL  = %s", settings.FILE_SERVICE_URL)
    _log.debug("  RESOLVE_FILE_REFS = %s", settings.RESOLVE_FILE_REFS)
    _log.debug("============================")

    # 1. Create infrastructure (Layer 4)
    available_dependencies: dict[str, Any] = {
        "logger": StandardLogger(),
        "monitor": PrometheusMonitor(),
        "app_config": settings,
        "storage": LocalStorage(),
        "input_reader": LocalInputReader(),
        "output_writer": LocalOutputWriter(),
        "worker_registry": HttpWorkerRegistry(),
    }

    # File reference resolver (only when file service URL is configured)
    if settings.FILE_SERVICE_URL and settings.RESOLVE_FILE_REFS:
        available_dependencies["file_ref_resolver"] = HttpFileRefResolver(
            file_service_url=settings.FILE_SERVICE_URL,
            max_input_bytes=settings.RESOLVE_MAX_INPUT_BYTES,
            stream_collections=settings.WORKER_STREAM_FILE_BACKED_INPUTS,
            window_size=settings.RESOLVE_WINDOW_ROWS,
        )

    # Function registry
    from worker_sdk.layer2_application.services.function_registry import FunctionRegistry
    func_registry = FunctionRegistry()
    if functions:
        for func in functions:
            func_registry.register(func)
    available_dependencies["function_registry"] = func_registry

    if extra_dependencies:
        available_dependencies.update(extra_dependencies)

    # 2. Discover application use-case classes (Layer 2)
    feature_classes = scan_and_load_features(features_path, base_module)

    # 3. Smart injection: only inject what each UseCase asks for
    container: dict[str, Any] = {}
    for name, UseCaseClass in feature_classes.items():
        sig = inspect.signature(UseCaseClass)
        injectable_kwargs = {
            param: dep
            for param, dep in available_dependencies.items()
            if param in sig.parameters
        }
        container[name] = UseCaseClass(**injectable_kwargs)

    # Build node type registry for multi-type workers
    if node_types:
        node_type_registry = {nt.worker_type: nt for nt in node_types}
        available_dependencies["node_type_registry"] = node_type_registry
        _log.debug("Registered %d node types: %s", len(node_type_registry), list(node_type_registry.keys()))

    # Expose raw dependencies for lifecycle management (registration, heartbeat, etc.)
    container["_dependencies"] = available_dependencies

    return container
