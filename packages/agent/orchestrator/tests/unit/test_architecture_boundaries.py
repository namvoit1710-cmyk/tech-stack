"""Architecture boundary regression tests for the orchestrator."""

import ast
import sys
from pathlib import Path

import pytest

_BASE_ROOT = Path(__file__).parents[2]
_APP_ROOT = _BASE_ROOT / "app"

_LAYER1_DIR = _APP_ROOT / "layer1_domain"
_LAYER2_DIR = _APP_ROOT / "layer2_application"
_LAYER3_DIR = _APP_ROOT / "layer3_adapters"

_STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names)

_LAYER2_FORBIDDEN_PREFIXES = (
    "app.layer3_adapters",
    "app.layer4_frameworks",
)

_LAYER3_FORBIDDEN_PREFIXES = ("app.layer4_frameworks",)
_LAYER1_ALLOWED_EXTERNALS = {"agent_sdk"}
_LAYER1_ALLOWED_INTERNAL_IMPORTS = {
    "app.layer1_domain.entities.planner.request_planner": {
        "app.layer4_frameworks.ai.openai_llm_provider",
    },
    "app.layer1_domain.interfaces.llm_provider_interface": {
        "app.layer4_frameworks.ai.openai_llm_provider",
    },
}
_LAYER2_ALLOWED_OUTER_IMPORTS = {
    "app.layer2_application.features.orchestrate_graph.use_cases.orchestrate_graph_use_case": {
        "app.layer4_frameworks.ai.openai_llm_provider",
    },
}


def _collect_python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


def _collect_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _top_level(module: str) -> str:
    return module.split(".")[0]


def _starts_with_any(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(_BASE_ROOT).with_suffix("").parts)


class TestLayer1OnlyImportsDomainOrStdlib:
    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER1_DIR))
    def test_layer1_stays_framework_free(self, py_file: Path):
        allowed_internal_imports = _LAYER1_ALLOWED_INTERNAL_IMPORTS.get(_module_name(py_file), set())
        violations = []
        for module in _collect_imports(py_file):
            top = _top_level(module)
            if top == "app":
                if module in allowed_internal_imports:
                    continue
                if not (module == "app.layer1_domain" or module.startswith("app.layer1_domain.")):
                    violations.append(module)
            elif top not in _STDLIB_MODULES and top not in _LAYER1_ALLOWED_EXTERNALS:
                violations.append(module)
        rel = py_file.relative_to(_BASE_ROOT)
        assert not violations, f"{rel}: Layer 1 imports disallowed modules: {violations}"


class TestLayer2DoesNotImportOuterLayers:
    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER2_DIR))
    def test_layer2_imports_do_not_leak_outward(self, py_file: Path):
        allowed_outer_imports = _LAYER2_ALLOWED_OUTER_IMPORTS.get(_module_name(py_file), set())
        violations = []
        for module in _collect_imports(py_file):
            if module in allowed_outer_imports:
                continue
            if _starts_with_any(module, _LAYER2_FORBIDDEN_PREFIXES):
                violations.append(module)
        rel = py_file.relative_to(_BASE_ROOT)
        assert not violations, f"{rel}: Layer 2 imports forbidden modules: {violations}"


class TestLayer3DoesNotImportLayer4:
    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER3_DIR))
    def test_layer3_transport_stays_decoupled_from_frameworks(self, py_file: Path):
        violations = []
        for module in _collect_imports(py_file):
            if _starts_with_any(module, _LAYER3_FORBIDDEN_PREFIXES):
                violations.append(module)
        rel = py_file.relative_to(_BASE_ROOT)
        assert not violations, f"{rel}: Layer 3 imports Layer 4 modules: {violations}"