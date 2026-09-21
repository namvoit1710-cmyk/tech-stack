from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import HitlInterruptPayload
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentInput,
    ResumeAgentOutput,
    ResumeAgentUseCase,
)
from agent_sdk.layer2_application.interfaces.agent_endpoint_resolver import (
    IAgentEndpointResolver,
)
from agent_sdk.layer4_frameworks.http.agent_call_coordinator import (
    AgentCallCoordinator,
)


class StubEndpointResolver(IAgentEndpointResolver):
    def __init__(self, mapping: dict):
        self._mapping = mapping

    async def resolve_endpoint(self, agent_id: str) -> str:
        if agent_id not in self._mapping:
            raise ValueError(f"Unknown agent_id: {agent_id}")
        return self._mapping[agent_id]


def _agent_call_output(
    agent_id: str,
    input_data: str = "{}",
    thread_id: str = "t1",
    interrupt_id: str = "i1",
) -> ExecuteAgentOutput:
    return ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            value={"type": "AGENT_CALL", "agent_id": agent_id, "input": input_data},
        ),
    )


async def test_full_flow_execute_interrupt_resume():
    """Full integration: execute → AGENT_CALL interrupt → sub-agent call → resume → success."""
    sub_agent_response = {
        "message": "File processed",
        "status": "success",
        "agent_data": {"file_id": "file-abc-123"},
        "agent_result": {
            "file_id": "file-abc-123",
            "content": "a,b\n1,2",
            "message": "ok",
        },
    }

    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_agent_call_output("file-agent", '{"message": "process file"}')
    )

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(
            message="Workflow completed with file processing",
            status="success",
            agent_data={"processed": True},
        )
    )

    resolver = StubEndpointResolver({"file-agent": "http://file-agent.svc:8080"})

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = sub_agent_response
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="process my file")
    )

    assert result.status == "success"
    assert "file" in result.message.lower()

    post_call = mock_client.post.call_args
    assert post_call[0][0] == "http://file-agent.svc:8080/api/v1/execute"
    assert post_call[1]["json"] == {"message": "process file"}

    resume_call = mock_resume_uc.execute.call_args[0][0]
    assert isinstance(resume_call, ResumeAgentInput)
    assert resume_call.thread_id == "t1"
    assert resume_call.interrupt_id == "i1"
    assert resume_call.resume_value == sub_agent_response


async def test_business_data_flows_verbatim_through_coordinator():
    """Verify that structured business data (file_id, content) passes through without transformation."""
    critical_response = {
        "message": "File Agent execute successfully.",
        "status": "success",
        "agent_data": {"file_id": "storage-key-789"},
        "agent_result": {
            "file_id": "storage-key-789",
            "content": "col1,col2\na,b\nc,d",
            "message": "ok",
        },
    }

    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_agent_call_output("modify-file-agent")
    )

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="done", status="success")
    )

    resolver = StubEndpointResolver({"modify-file-agent": "http://modify-file:8080"})

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = critical_response
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="modify the file")
    )

    resume_input = mock_resume_uc.execute.call_args[0][0]
    assert resume_input.resume_value == critical_response
    assert resume_input.resume_value["agent_result"]["file_id"] == "storage-key-789"
    assert resume_input.resume_value["agent_result"]["content"] == "col1,col2\na,b\nc,d"


async def test_coordinator_chain_workflow_agent_then_file_agent():
    """Simulates: workflow-agent → AGENT_CALL(file-agent) → resume → success."""
    file_agent_response = {
        "message": "File created",
        "status": "success",
        "agent_data": {"file_id": "new-file-456"},
    }

    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_agent_call_output(
            "file-agent", '{"message": "create report"}', thread_id="thread-xyz"
        )
    )

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="Report generated", status="success")
    )

    resolver = StubEndpointResolver({"file-agent": "http://file-agent:9090"})

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = file_agent_response
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc,
        ExecuteAgentInput(message="generate report", conv_id="thread-xyz"),
    )

    assert result.status == "success"
    resume_in = mock_resume_uc.execute.call_args[0][0]
    assert resume_in.thread_id == "thread-xyz"
    assert resume_in.resume_value["agent_data"]["file_id"] == "new-file-456"


async def test_stub_endpoint_resolver_contract():
    """Verify IAgentEndpointResolver interface is satisfied by StubEndpointResolver."""
    resolver = StubEndpointResolver({"my-agent": "http://my-agent:8080"})
    url = await resolver.resolve_endpoint("my-agent")
    assert url == "http://my-agent:8080"

    with pytest.raises(ValueError):
        await resolver.resolve_endpoint("unknown-agent")


async def test_coordinator_error_recovery_continues_on_sub_agent_failure():
    """When sub-agent transport fails, coordinator returns terminal error without resuming."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_agent_call_output("failing-agent")
    )

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(
            message="Handled sub-agent failure gracefully",
            status="success",
        )
    )

    resolver = StubEndpointResolver({"failing-agent": "http://failing-agent:8080"})

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "500 Server Error", request=MagicMock(), response=MagicMock()
        )
    )

    coordinator = AgentCallCoordinator(
        endpoint_resolver=resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="trigger failing agent")
    )

    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error is not None
