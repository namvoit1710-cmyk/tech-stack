import pkgutil
import importlib
import inspect
from typing import Dict, Any, Optional
import logging

_logger = logging.getLogger(__name__)

FEATURE_REGISTRY: Dict[str, Any] = {}
USECASE_CONTAINER: Dict[str, Any] = {}


def _to_pascal_case(snake: str) -> str:
    """Convert snake_case to PascalCase: 'start_run' -> 'StartRun'."""
    return "".join(word.capitalize() for word in snake.split("_"))


def _register_use_case(registry_key: str, use_case_class: Any) -> None:
    FEATURE_REGISTRY[registry_key] = use_case_class
    _logger.info(f"   [+] Loaded: {registry_key}")


def _load_legacy_feature_use_case(feature_name: str, feature_module: Any) -> None:
    expected_class_name = f"{_to_pascal_case(feature_name)}UseCase"
    if hasattr(feature_module, expected_class_name):
        _register_use_case(feature_name, getattr(feature_module, expected_class_name))
    elif hasattr(feature_module, "UseCase"):
        _register_use_case(feature_name, feature_module.UseCase)


def _load_nested_use_cases(feature_name: str) -> None:
    if feature_name == "orchestrate_graph":
        module = importlib.import_module(
            f"{__name__}.{feature_name}.use_cases.orchestrate_graph_use_case"
        )
        if hasattr(module, "OrchestrateGraphUseCase"):
            _register_use_case(feature_name, getattr(module, "OrchestrateGraphUseCase"))
        return

    use_cases_module_name = f"{__name__}.{feature_name}.use_cases"
    try:
        use_cases_module = importlib.import_module(use_cases_module_name)
    except ModuleNotFoundError:
        return

    for _, module_name, is_pkg in pkgutil.iter_modules(use_cases_module.__path__):
        if is_pkg:
            continue
        if not module_name.endswith("_use_case"):
            continue

        full_module_name = f"{use_cases_module_name}.{module_name}"
        module = importlib.import_module(full_module_name)
        action_name = module_name.removesuffix("_use_case")
        expected_class_name = f"{_to_pascal_case(action_name)}UseCase"

        if hasattr(module, expected_class_name):
            use_case_class = getattr(module, expected_class_name)
        elif hasattr(module, "UseCase"):
            use_case_class = module.UseCase
        else:
            use_case_classes = [
                member
                for _, member in inspect.getmembers(module, inspect.isclass)
                if member.__module__ == module.__name__ and member.__name__.endswith("UseCase")
            ]
            if len(use_case_classes) != 1:
                _logger.warning(f"   [!] Skipped {feature_name}.{action_name}: no use case class found")
                continue
            use_case_class = use_case_classes[0]

        registry_key = f"{feature_name}.{action_name}"
        _register_use_case(registry_key, use_case_class)

        if action_name == feature_name and feature_name not in FEATURE_REGISTRY:
            _register_use_case(feature_name, use_case_class)


def load_features(logger: Optional[Any] = None) -> Dict[str, Any]:
    package_path = __path__
    active_logger = logger or _logger
    active_logger.info(f"Scanning features in: {package_path}")
    FEATURE_REGISTRY.clear()

    for _, module_name, is_pkg in sorted(pkgutil.iter_modules(package_path), key=lambda item: item[1]):
        if is_pkg:
            try:
                full_module_name = f"{__name__}.{module_name}"
                module = importlib.import_module(full_module_name)
                _load_legacy_feature_use_case(module_name, module)
                _load_nested_use_cases(module_name)
            except Exception as e:
                active_logger.error(f"   [!] Failed {module_name}: {e}")

    return FEATURE_REGISTRY


def load_all_usecases(
    dependencies: dict[str, Any],
    logger: Optional[Any] = None,
    *,
    reload: bool = False,
) -> Dict[str, Any]:
    active_logger = logger or _logger
    if reload:
        USECASE_CONTAINER.clear()

    if not FEATURE_REGISTRY:
        load_features(active_logger)

    if not USECASE_CONTAINER:
        for name, use_case_class in sorted(FEATURE_REGISTRY.items()):
            signature = inspect.signature(use_case_class)
            injectable = {
                param_name: dependency
                for param_name, dependency in dependencies.items()
                if param_name in signature.parameters
            }
            USECASE_CONTAINER[name] = use_case_class(**injectable)

    active_logger.info(f"Loaded use cases: {list(USECASE_CONTAINER.keys())}")
    return dict(USECASE_CONTAINER)


def get_usecase(name: str):
    usecase = USECASE_CONTAINER.get(name)
    if usecase is None:
        raise KeyError(f"Use case '{name}' is not registered")
    return usecase
