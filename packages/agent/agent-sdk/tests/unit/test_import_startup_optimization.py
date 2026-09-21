"""Regression tests for agent_sdk import startup behaviour.

These tests guard against import-time side-effects that slow down plain
``import agent_sdk`` by eagerly pulling in heavy third-party packages.

The tests are intentionally subprocess-based so that each assertion runs in a
pristine interpreter with no shared module cache.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SDK_ROOT = str(Path(__file__).parent.parent.parent)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCRIPT_HEAVY_MODULES = """
import sys
import json
import agent_sdk

heavy = [
    "agent_sdk.runner",
    "agent_sdk.bootstrap",
    "uvicorn",
    "fastapi",
    "openai",
    "langchain_openai",
    "simplemdg_mcp_client",
]
present = [m for m in heavy if m in sys.modules]
print(json.dumps(present))
"""

_SCRIPT_ROOT_IMPORTS = """
from agent_sdk import OpenAIService, tool, run_agent
print("ok")
"""

_SCRIPT_CONFIG_IMPORTS = """
from agent_sdk.layer4_frameworks.config import HanaCredentials, get_hana_credentials
print("ok")
"""


def _run_python(script: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{_SDK_ROOT}{os.pathsep}{existing}" if existing else _SDK_ROOT
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Heavy-module leak tests (single subprocess, parameterized per module)
# ---------------------------------------------------------------------------

_HEAVY_MODULES = [
    "agent_sdk.runner",
    "agent_sdk.bootstrap",
    "uvicorn",
    "fastapi",
    "openai",
    "langchain_openai",
    "simplemdg_mcp_client",
]


@pytest.fixture(scope="module")
def _heavy_modules_present() -> list[str]:
    result = _run_python(_SCRIPT_HEAVY_MODULES)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize("module_name", _HEAVY_MODULES)
def test_plain_import_does_not_load_heavy_module(
    module_name: str, _heavy_modules_present: list[str]
) -> None:
    """``import agent_sdk`` must not pull in any heavy third-party module."""
    assert module_name not in _heavy_modules_present, (
        f"{module_name!r} was loaded by plain import agent_sdk; "
        f"all heavy modules present: {_heavy_modules_present}"
    )


# ---------------------------------------------------------------------------
# Root public-API regression tests
# ---------------------------------------------------------------------------


def test_root_exports_openai_service_tool_run_agent():
    """``from agent_sdk import OpenAIService, tool, run_agent`` must succeed."""
    result = _run_python(_SCRIPT_ROOT_IMPORTS)
    assert (
        result.returncode == 0
    ), f"Root import failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# Nested config re-export regression tests
# ---------------------------------------------------------------------------


def test_config_layer_exports_hana_credentials_and_get_hana_credentials():
    """``from agent_sdk.layer4_frameworks.config import HanaCredentials, get_hana_credentials`` must succeed."""
    result = _run_python(_SCRIPT_CONFIG_IMPORTS)
    assert (
        result.returncode == 0
    ), f"Config import failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip() == "ok"
