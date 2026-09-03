"""AST-based architecture boundary regression tests.

Verifies that the layered architecture dependency rules are enforced:
- Layer 1 (Domain): may only import stdlib modules.
- Layer 2 (Application): may not import layer3, layer4, or framework packages.
- Layer 3 (Adapters): may not import layer4.

Composition-root files (agent_sdk/bootstrap.py, agent_sdk/runner.py) are
intentionally excluded from these checks because they are permitted to wire
all layers together.
"""

import ast
import sys
from pathlib import Path

import pytest

_SDK_ROOT = Path(__file__).parents[2] / "agent_sdk"

_LAYER1_DIR = _SDK_ROOT / "layer1_domain"
_LAYER2_DIR = _SDK_ROOT / "layer2_application"
_LAYER3_DIR = _SDK_ROOT / "layer3_adapters"
_LAYER4_HANA_DIR = _SDK_ROOT / "layer4_frameworks" / "persistence" / "hana"

_STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names)

_LAYER2_FORBIDDEN_PREFIXES = (
    "agent_sdk.layer3_adapters",
    "agent_sdk.layer4_frameworks",
    "langgraph",
    "langchain_core",
    "langchain_openai",
    "openai",
    "httpx",
    "pydantic",
    "tiktoken",
)

_LAYER3_FORBIDDEN_PREFIXES = ("agent_sdk.layer4_frameworks",)


def _collect_python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


def _collect_imports(path: Path) -> list[str]:
    """Return all fully-qualified module names imported in *path* via AST."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.append(node.module)
    return modules


def _top_level(module: str) -> str:
    return module.split(".")[0]


def _starts_with_any(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


class TestLayer1OnlyImportsStdlib:
    """Layer 1 (Domain) must only import from the Python standard library or itself."""

    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER1_DIR))
    def test_no_non_stdlib_imports(self, py_file: Path):
        violations = []
        for module in _collect_imports(py_file):
            top = _top_level(module)
            if top == "pydantic":
                continue  # Allow pydantic in Layer 1
            if top == "agent_sdk":
                if not (
                    module == "agent_sdk.layer1_domain"
                    or module.startswith("agent_sdk.layer1_domain.")
                ):
                    violations.append(module)
            elif top not in _STDLIB_MODULES:
                violations.append(module)
        rel = py_file.relative_to(_SDK_ROOT.parent)
        assert (
            not violations
        ), f"{rel}: Layer 1 file imports non-stdlib or non-layer1 modules: {violations}"


class TestLayer2NoFrameworkLeakage:
    """Layer 2 (Application) must not import layer3, layer4, or framework packages."""

    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER2_DIR))
    def test_no_forbidden_imports(self, py_file: Path):
        violations = []
        for module in _collect_imports(py_file):
            if py_file.name == "llm_service.py" and module == "pydantic":
                continue  # The LLM protocol uses a pydantic-bound structured model type.
            if _starts_with_any(module, _LAYER2_FORBIDDEN_PREFIXES):
                violations.append(module)
        rel = py_file.relative_to(_SDK_ROOT.parent)
        assert (
            not violations
        ), f"{rel}: Layer 2 file imports forbidden modules: {violations}"


class TestLayer3NoLayer4Imports:
    """Layer 3 (Adapters) must not import layer4 implementations directly."""

    @pytest.mark.parametrize("py_file", _collect_python_files(_LAYER3_DIR))
    def test_no_layer4_imports(self, py_file: Path):
        violations = []
        for module in _collect_imports(py_file):
            if _starts_with_any(module, _LAYER3_FORBIDDEN_PREFIXES):
                violations.append(module)
        rel = py_file.relative_to(_SDK_ROOT.parent)
        assert (
            not violations
        ), f"{rel}: Layer 3 file imports Layer 4 modules: {violations}"


class TestArchitecturePackageCoverage:
    """Regression coverage for newly introduced queue-first package locations."""

    def test_layer1_queue_and_shared_state_contract_modules_exist(self):
        expected = {
            _LAYER1_DIR / "entities" / "agent_runtime_config.py",
            _LAYER1_DIR / "entities" / "execution_policy.py",
            _LAYER1_DIR / "entities" / "queue_metadata.py",
            _LAYER1_DIR / "entities" / "shared_state.py",
        }

        assert expected.issubset(set(_collect_python_files(_LAYER1_DIR)))

    def test_layer2_utils_package_is_scanned_by_boundary_suite(self):
        expected = {
            _LAYER2_DIR / "utils" / "__init__.py",
            _LAYER2_DIR / "utils" / "definition.py",
            _LAYER2_DIR / "utils" / "dependency_resolver.py",
            _LAYER2_DIR / "utils" / "state_resolver.py",
            _LAYER2_DIR / "utils" / "state_snapshot.py",
        }

        assert expected.issubset(set(_collect_python_files(_LAYER2_DIR)))

    def test_layer2_queue_reaction_services_are_scanned_by_boundary_suite(self):
        expected = {
            _LAYER2_DIR / "services" / "async_agent_delegator.py",
            _LAYER2_DIR / "services" / "message_reaction" / "default_handlers.py",
            _LAYER2_DIR / "services" / "message_reaction" / "router.py",
        }

        assert expected.issubset(set(_collect_python_files(_LAYER2_DIR)))

    def test_layer4_hana_shared_state_repository_exists(self):
        expected = _LAYER4_HANA_DIR / "hana_shared_state_repository.py"

        assert expected.exists(), f"Missing Layer 4 HANA adapter: {expected}"
