from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from tests.helpers.testing import StubLogger, StubMonitor


class _CapturingExecuteUseCase:
    def __init__(self):
        self.request = None

    async def execute(self, request):
        from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
            ExecuteAgentOutput,
        )

        self.request = request
        return ExecuteAgentOutput(
            status="success", correlation_id=request.correlation_id
        )


class _MockConsumer:
    def __init__(self, message: dict):
        self._message = message

    async def start(self, handler):
        await handler(self._message)


class _MockPublisher:
    def __init__(self):
        self.published = []

    async def publish(self, topic: str, message: dict, key: str | None = None):
        self.published.append({"topic": topic, "message": message, "key": key})


@pytest.mark.asyncio
async def test_execute_use_case_passes_metadata_and_uploaded_files_to_graph_state_and_config():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"formatted_response": {"content": "ok", "status": "success"}}
    )
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=graph,
    )

    request = ExecuteAgentInput(
        message="hello",
        conv_id="main_exec_1",
        correlation_id="corr-exec-1",
        metadata=ConversationMetadata(
            main_conv_id="main_exec_1",
            sub_conv_ids=["sub_exec_1"],
            uploaded_file_ids=["file-1", "file-1", "  "],
        ),
    )

    await use_case.execute(request)

    (initial_state,) = graph.ainvoke.call_args.args[:1]
    config = (
        graph.ainvoke.call_args.kwargs.get("config") or graph.ainvoke.call_args.args[1]
    )

    assert initial_state["metadata"] == {
        "main_conv_id": "main_exec_1",
        "sub_conv_ids": ["sub_exec_1"],
        "uploaded_file_ids": ["file-1"],
    }
    assert initial_state["uploaded_file_ids"] == ["file-1"]
    assert config["configurable"]["thread_id"] == "main_exec_1"
    assert config["configurable"]["conv_id"] == "main_exec_1"
    assert config["configurable"]["correlation_id"] == "corr-exec-1"
    assert config["configurable"]["metadata"] == initial_state["metadata"]
    assert config["configurable"]["uploaded_file_ids"] == ["file-1"]


@pytest.mark.asyncio
async def test_resume_use_case_passes_metadata_and_uploaded_files_to_runtime_config():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )
    from agent_sdk.layer2_application.interfaces.agent_runtime import AgentRuntimeResult

    runtime = MagicMock()
    runtime.resume = AsyncMock(
        return_value=AgentRuntimeResult(
            output={"formatted_response": {"content": "ok", "status": "success"}}
        )
    )
    use_case = ResumeAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_runtime=runtime,
    )

    request = ResumeAgentInput(
        thread_id="sub_resume_1",
        resume_value="approved",
        correlation_id="corr-resume-1",
        metadata=ConversationMetadata(
            main_conv_id="main_resume_1",
            sub_conv_ids=["sub_resume_1"],
            uploaded_file_ids=["file-2"],
        ),
        uploaded_file_ids=["file-2"],
    )

    await use_case.execute(request)

    _, config = runtime.resume.call_args.args[:2]
    assert config["configurable"]["thread_id"] == "sub_resume_1"
    assert config["configurable"]["conv_id"] == "sub_resume_1"
    assert config["configurable"]["correlation_id"] == "corr-resume-1"
    assert config["configurable"]["metadata"] == {
        "main_conv_id": "main_resume_1",
        "sub_conv_ids": ["sub_resume_1"],
        "uploaded_file_ids": ["file-2"],
    }
    assert config["configurable"]["uploaded_file_ids"] == ["file-2"]


def test_execute_route_maps_metadata_contract_to_execute_input():
    from fastapi.testclient import TestClient

    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    use_case = _CapturingExecuteUseCase()
    client = TestClient(create_agent_app({"execute_agent": use_case}))

    response = client.post(
        "/api/v1/execute",
        json={
            "message": "hello",
            "conv_id": "main_http_1",
            "correlation_id": "corr-http-1",
            "uploaded_file_ids": ["file-http-1"],
            "metadata": {
                "main_conv_id": "main_http_1",
                "sub_conv_ids": ["sub_http_1"],
                "uploaded_file_ids": ["file-http-1"],
            },
        },
    )

    assert response.status_code == 200
    assert use_case.request is not None
    assert use_case.request.metadata == ConversationMetadata(
        main_conv_id="main_http_1",
        sub_conv_ids=["sub_http_1"],
        uploaded_file_ids=["file-http-1"],
    )
    assert use_case.request.uploaded_file_ids == ["file-http-1"]


@pytest.mark.asyncio
async def test_consumer_maps_metadata_contract_to_execute_input():
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    use_case = _CapturingExecuteUseCase()
    publisher = _MockPublisher()
    await run_consumer_agent(
        {
            "execute_agent": use_case,
            "consumer": _MockConsumer(
                {
                    "message": "hello",
                    "conversation_id": "main_queue_1",
                    "correlation_id": "corr-queue-1",
                    "reply_to": "agent.responses",
                    "uploaded_file_ids": ["file-queue-1"],
                    "metadata": {
                        "main_conv_id": "main_queue_1",
                        "sub_conv_ids": ["sub_queue_1"],
                        "uploaded_file_ids": ["file-queue-1"],
                    },
                }
            ),
            "publisher": publisher,
            "logger": StubLogger(),
        }
    )

    assert use_case.request is not None
    assert use_case.request.conv_id == "main_queue_1"
    assert use_case.request.metadata == ConversationMetadata(
        main_conv_id="main_queue_1",
        sub_conv_ids=["sub_queue_1"],
        uploaded_file_ids=["file-queue-1"],
    )
    assert use_case.request.uploaded_file_ids == ["file-queue-1"]


@pytest.mark.asyncio
async def test_execute_request_metadata_normalizes_scalar_uploaded_file_id():
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    use_case = _CapturingExecuteUseCase()
    publisher = _MockPublisher()
    await run_consumer_agent(
        {
            "execute_agent": use_case,
            "consumer": _MockConsumer(
                {
                    "message": "hello",
                    "conversation_id": "main_queue_scalar_1",
                    "correlation_id": "corr-queue-scalar-1",
                    "reply_to": "agent.responses",
                    "uploaded_file_ids": "file-queue-scalar-1",
                }
            ),
            "publisher": publisher,
            "logger": StubLogger(),
        }
    )

    assert use_case.request is not None
    assert use_case.request.uploaded_file_ids == ["file-queue-scalar-1"]
