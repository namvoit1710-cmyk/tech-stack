"""Architecture boundary regression tests for the business-agent-builder service."""

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
    "langgraph",
    "langchain_core",
    "langchain_openai",
    "openai",
    "httpx",
    "pydantic",
    "pydantic_settings",
)

_LAYER2_ALLOWED_IMPORTS: set[str] = set()

_LAYER3_FORBIDDEN_PREFIXES = ("app.layer4_frameworks",)


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


class TestLayer1OnlyImportsStdlibOrSdkDomain:
    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER1_DIR))
    def test_no_non_domain_imports(self, py_file: Path):
        violations = []
        for module in _collect_imports(py_file):
            top = _top_level(module)
            if top == "app":
                if not (
                    module == "app.layer1_domain"
                    or module.startswith("app.layer1_domain.")
                ):
                    violations.append(module)
            elif top == "agent_sdk":
                if not (
                    module == "agent_sdk.layer1_domain"
                    or module.startswith("agent_sdk.layer1_domain.")
                ):
                    violations.append(module)
            elif top not in _STDLIB_MODULES:
                violations.append(module)
        rel = py_file.relative_to(_BASE_ROOT)
        assert not violations, f"{rel}: Layer 1 imports disallowed modules: {violations}"


class TestLayer2NoFrameworkLeakage:
    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER2_DIR))
    def test_no_forbidden_imports(self, py_file: Path):
        violations = []
        for module in _collect_imports(py_file):
            if module in _LAYER2_ALLOWED_IMPORTS:
                continue
            if _starts_with_any(module, _LAYER2_FORBIDDEN_PREFIXES):
                violations.append(module)
        rel = py_file.relative_to(_BASE_ROOT)
        assert not violations, f"{rel}: Layer 2 imports forbidden modules: {violations}"


class TestLayer3NoLayer4Imports:
    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER3_DIR))
    def test_no_layer4_imports(self, py_file: Path):
        violations = []
        for module in _collect_imports(py_file):
            if _starts_with_any(module, _LAYER3_FORBIDDEN_PREFIXES):
                violations.append(module)
        rel = py_file.relative_to(_BASE_ROOT)
        assert not violations, f"{rel}: Layer 3 imports Layer 4 modules: {violations}"


def test_optional_layer3_directory_can_be_absent_in_current_blueprint():
    assert not _LAYER3_DIR.exists() or _LAYER3_DIR.is_dir()