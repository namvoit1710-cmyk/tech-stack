import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

from tests.helpers.testing import StubLogger as _StubLogger
from tests.helpers.testing import StubMonitor as _StubMonitor


def test_echo_agent_full_pipeline(monkeypatch):
    monkeypatch.syspath_prepend(
        os.path.join(os.path.dirname(__file__), "..", "examples", "echo_agent")
    )
    import app.layer2_application.echo_nodes as echo_nodes
    from echo_agent.app.layer4_frameworks.graph.echo_graph_builder import (
        build_echo_graph,
    )

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    monkeypatch.setattr(echo_nodes, "interrupt", lambda *args, **kwargs: "approved")

    graph = build_echo_graph()
    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=graph
    )
    result = asyncio.run(
        use_case.execute(
            ExecuteAgentInput(
                message="Hello echo", conv_id="test-conv", user_id="test-user"
            )
        )
    )
    assert result.error is None
    assert result.status == "success"
    assert result.message == "[echo] Hello echo"


def test_echo_agent_info(monkeypatch):
    monkeypatch.delenv("AGENT_TYPE", raising=False)
    monkeypatch.delenv("AGENT_DOMAIN", raising=False)
    # Reload the module to pick up the changes
    import importlib

    import echo_agent.app.layer4_frameworks.config.app_config

    importlib.reload(echo_agent.app.layer4_frameworks.config.app_config)
    from echo_agent.app.layer4_frameworks.config.app_config import settings

    assert settings.AGENT_TYPE == "echo"
    assert settings.AGENT_DOMAIN == "examples"
