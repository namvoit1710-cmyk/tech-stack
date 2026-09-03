import os
import pathlib
import sys
from unittest.mock import MagicMock, patch


def test_build_app_container_succeeds():
    echo_agent_path = str(
        pathlib.Path(__file__).parent.parent / "examples" / "echo_agent"
    )
    sys.path.insert(0, echo_agent_path)
    # Capture pre-test env state so we can restore it precisely
    _prev_messaging = os.environ.get("MESSAGING_MODE", _UNSET := object())
    _prev_openai = os.environ.get("OPENAI_API_KEY", _UNSET)
    _prev_llm_model = os.environ.get("LLM_MODEL", _UNSET)
    try:
        os.environ["MESSAGING_MODE"] = "mock"
        os.environ["LLM_MODEL"] = "gpt-4o-mini"
        os.environ["OPENAI_API_KEY"] = "dummy"
        from bootstrap import build_app_container

        chat_client = MagicMock()
        graph_sentinel = MagicMock()
        with (
            patch(
                "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model",
                return_value=chat_client,
            ) as mock_init_chat_model,
            patch(
                "bootstrap.build_echo_graph",
                return_value=graph_sentinel,
            ) as mock_build_echo_graph,
        ):
            container = build_app_container()
        assert container is not None, "build_app_container() must return a container"
        deps = container["_dependencies"]
        assert deps["llm"] is chat_client
        build_graph_kwargs = mock_build_echo_graph.call_args.kwargs
        assert build_graph_kwargs["checkpointer"] is not None
        assert build_graph_kwargs["deps"]["openai_service"] is not None
        assert build_graph_kwargs["deps"][
            "worker_tools"
        ], "build_app_container() must pass local tools to build_echo_graph()"
        assert mock_init_chat_model.call_count == 1
        init_args = mock_init_chat_model.call_args.args
        init_kwargs = mock_init_chat_model.call_args.kwargs
        assert init_args == ("gpt-4o-mini",)
        assert init_kwargs["model_provider"] is None
        assert init_kwargs["temperature"] == 0.01
        assert init_kwargs["timeout"] is None
        assert init_kwargs["max_tokens"] is None
        assert init_kwargs["max_retries"] == 6
        assert "reasoning" not in init_kwargs
        assert "reasoning_effort" not in init_kwargs
    finally:
        if echo_agent_path in sys.path:
            sys.path.remove(echo_agent_path)
        if "bootstrap" in sys.modules:
            del sys.modules["bootstrap"]
        # Restore MESSAGING_MODE
        if _prev_messaging is _UNSET:
            os.environ.pop("MESSAGING_MODE", None)
        else:
            os.environ["MESSAGING_MODE"] = _prev_messaging
        # Restore OPENAI_API_KEY
        if _prev_openai is _UNSET:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = _prev_openai
        # Restore LLM_MODEL
        if _prev_llm_model is _UNSET:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = _prev_llm_model
