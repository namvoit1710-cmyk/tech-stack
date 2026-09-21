import importlib
import inspect
import logging
import pkgutil
from typing import Any, Dict, Optional
from app.layer2_application.interfaces.logger_interface import ILogger


FEATURE_REGISTRY: Dict[str, Any] = {}
USECASE_CONTAINER: Dict[str, Any] = {}


def _to_pascal_case(snake: str) -> str:
    return "".join(word.capitalize() for word in snake.split("_"))


def load_features(logger: Optional[ILogger] = None) -> Dict[str, Any]:
    FEATURE_REGISTRY.clear()

    for _, module_name, is_pkg in pkgutil.iter_modules(__path__):
        if is_pkg:
            continue

        try:
            module = importlib.import_module(f"{__name__}.{module_name}")
            expected_class_name = f"{_to_pascal_case(module_name.removesuffix('_usecase'))}UseCase"

            if hasattr(module, expected_class_name):
                FEATURE_REGISTRY[module_name.removesuffix("_usecase")] = getattr(module, expected_class_name)
            elif hasattr(module, "UseCase"):
                FEATURE_REGISTRY[module_name.removesuffix("_usecase")] = module.UseCase
            else:
                if logger:
                    logger.warning(f"No matching use case class found in module '{module_name}'")
        except Exception as exc:
            if logger:
                logger.error(f"Failed to load use case module '{module_name}': {exc}", exc_info=True)

    return FEATURE_REGISTRY


def load_all_usecases(dependencies: dict, logger: Optional[ILogger] = None) -> Dict[str, Any]:
    if not USECASE_CONTAINER:
        for name, UseCaseClass in FEATURE_REGISTRY.items():
            sig = inspect.signature(UseCaseClass)
            injectable = {
                p: d for p, d in dependencies.items()
                if p in sig.parameters
            }
            USECASE_CONTAINER[name] = UseCaseClass(**injectable)
    
    logger.info(f"Loaded use cases: {list(USECASE_CONTAINER.keys())}") if logger else None

    return USECASE_CONTAINER


def get_usecase(name: str):
    usecase_class = USECASE_CONTAINER.get(name)
    if usecase_class is None:
        raise KeyError(f"Use case '{name}' is not registered")

    return usecase_class
