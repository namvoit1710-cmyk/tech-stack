import importlib
import inspect
import logging
import pkgutil
from collections.abc import Callable, Iterable
from types import ModuleType
from typing import Any, cast

from agent_sdk import tool

from app.layer2_application.interfaces.register_tool import APP_TOOL_METADATA

logger = logging.getLogger(__name__)

TOOL_ROOT_PACKAGE = "app.layer2_application.tools"


def _iter_tool_modules(root_package: str = TOOL_ROOT_PACKAGE) -> list[ModuleType]:
    package = importlib.import_module(root_package)
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return []

    modules: list[ModuleType] = []
    for module_info in pkgutil.walk_packages(package_path, prefix=f"{root_package}."):
        try:
            modules.append(importlib.import_module(module_info.name))
        except Exception:
            logger.exception("Failed to import tool module %s", module_info.name)

    return modules


def _is_tool_candidate(func: Any) -> bool:
    if not (inspect.isfunction(func) or inspect.iscoroutinefunction(func)):
        return False

    return bool(getattr(func, APP_TOOL_METADATA, False))


def _wrap_marked_tool_functions(
    modules: Iterable[ModuleType],
    tool_decorator: Callable[[Callable[..., Any]], Any] = tool,
) -> list[Any]:
    registered_tools: list[Any] = []
    seen: set[tuple[str, str]] = set()

    for module in modules:
        for _, func in inspect.getmembers(module, inspect.isroutine):
            if not _is_tool_candidate(func):
                continue

            tool_func = cast(Callable[..., Any], func)

            identity = (tool_func.__module__, tool_func.__qualname__)
            if identity in seen:
                continue

            seen.add(identity)
            registered_tools.append(tool_decorator(tool_func))

    return registered_tools


def scan_all_tools(
    root_package: str = TOOL_ROOT_PACKAGE,
) -> list[Any]:
    tool_modules = _iter_tool_modules(root_package=root_package)
    logger.debug(
        "Scanned %s tool modules under package '%s': %s",
        len(tool_modules),
        root_package,
        [module.__name__ for module in tool_modules],
    )
    return _wrap_marked_tool_functions(tool_modules)