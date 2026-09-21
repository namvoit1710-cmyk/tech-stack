import importlib
import sys
from pathlib import Path
from textwrap import dedent
from types import ModuleType


def _install_fake_agent_sdk(monkeypatch):
    fake_agent_sdk = ModuleType("agent_sdk")

    def fake_tool(func):
        return {"wrapped": func.__name__, "func": func}

    fake_agent_sdk.tool = fake_tool
    monkeypatch.setitem(sys.modules, "agent_sdk", fake_agent_sdk)


def _create_package(tmp_path: Path, module_name: str, module_body: str) -> str:
    package_root = tmp_path / f"tool_registry_pkg_{module_name}"
    package_root.mkdir(parents=True)

    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / f"{module_name}.py").write_text(dedent(module_body), encoding="utf-8")

    return package_root.name


def _reload_tool_modules():
    for module_name in (
        "app.layer2_application.interfaces.register_tool",
        "app.layer4_frameworks.tools.tool_registry",
    ):
        sys.modules.pop(module_name, None)


def test_regist_tool_marks_function(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    _reload_tool_modules()

    regist_tool_module = importlib.import_module("app.layer2_application.interfaces.register_tool")

    @regist_tool_module.tool
    async def execute():
        return "ok"

    assert getattr(execute, regist_tool_module.APP_TOOL_METADATA, False) is True


def test_scan_all_tools_wraps_all_marked_functions(tmp_path, monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    _reload_tool_modules()

    package_name = _create_package(
        tmp_path,
        "sample_usecase",
        """
from app.layer2_application.interfaces.register_tool import tool


@tool
async def execute():
    return \"execute\"


@tool
async def run():
    return \"run\"


async def call():
    return \"call\"
""",
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    tool_registry = importlib.import_module("app.layer4_frameworks.tools.tool_registry")

    tools = tool_registry.scan_all_tools(root_package=package_name)

    assert [tool["wrapped"] for tool in tools] == ["execute", "run"]


def test_scan_all_tools_includes_marked_function_with_any_name(tmp_path, monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    _reload_tool_modules()

    package_name = _create_package(
        tmp_path,
        "call_usecase",
        """
from app.layer2_application.interfaces.register_tool import tool


@tool
def call():
    return \"call\"
""",
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    tool_registry = importlib.import_module("app.layer4_frameworks.tools.tool_registry")

    tools = tool_registry.scan_all_tools(root_package=package_name)

    assert [tool["wrapped"] for tool in tools] == ["call"]