"""Auto-discovery scanner for feature use cases.

Scans this package for sub-packages that export a <FeatureName>UseCase class.
Convention: folder name 'start_run' -> class 'StartRunUseCase'.
"""

import logging
import pkgutil
import importlib
from typing import Any

logger = logging.getLogger(__name__)

FEATURE_REGISTRY: dict[str, Any] = {}


def _to_pascal_case(snake: str) -> str:
    """Convert snake_case to PascalCase: 'start_run' -> 'StartRun'."""
    return "".join(word.capitalize() for word in snake.split("_"))


def load_features() -> None:
    """Scan features/ for packages exporting <Name>UseCase classes.

    Populates FEATURE_REGISTRY in-place.
    """
    package_path = __path__
    base_module = __name__

    logger.info("Scanning features in: %s", package_path)

    for _, module_name, is_pkg in pkgutil.iter_modules(package_path):
        if is_pkg:
            try:
                full_module_name = f"{base_module}.{module_name}"
                module = importlib.import_module(full_module_name)

                expected_class_name = f"{_to_pascal_case(module_name)}UseCase"
                if hasattr(module, expected_class_name):
                    FEATURE_REGISTRY[module_name] = getattr(module, expected_class_name)
                    logger.info("Found Feature: %s", module_name)
            except Exception as e:
                logger.warning("Error loading %s: %s", module_name, e)


