from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import HitlInterruptPayload
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentOutput,
    ResumeAgentUseCase,
)
from agent_sdk.layer4_frameworks.http.agent_call_coordinator import (
    SUB_AGENT_TRANSPORT_ERROR_MESSAGE,
    AgentCallCoordinator,
)


def _make_interrupted_output(agent_id="sub-agent-1", input_data='{"message":"test"}'):
    return ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id="thread-1",
            interrupt_id="int-1",
            value={"type": "AGENT_CALL", "agent_id": agent_id, "input": input_data},
        ),
    )


def _make_success_output(message="done"):
    return ExecuteAgentOutput(message=message, status="success")


async def test_coordinator_detects_agent_call_and_resumes():
    """When execute returns AGENT_CALL interrupt, coordinator calls sub-agent and resumes."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="final", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://sub-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message": "sub-agent result",
        "status": "success",
        "agent_data": {"file_id": "new-123"},
    }
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_client.post.assert_called_once()
    mock_resume_uc.execute.assert_called_once()
    resume_input = mock_resume_uc.execute.call_args[0][0]
    assert resume_input.thread_id == "thread-1"
    assert resume_input.interrupt_id == "int-1"
    assert resume_input.resume_value == {
        "message": "sub-agent result",
        "status": "success",
        "agent_data": {"file_id": "new-123"},
    }
    assert result.status == "success"
    assert result.message == "final"


async def test_coordinator_extracts_correlation_fields_from_interrupt_payload():
    output = ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id="thread-9",
            interrupt_id="int-9",
            value={
                "type": "AGENT_CALL",
                "agent_id": "sub-agent-9",
                "agent_type": "planner",
                "input": {"message": "test"},
                "correlation_id": "corr-9",
                "reply_queue": "parent.reply",
                "session_id": "sess-9",
                "context_snapshot": {"shared_state": {"x": 1}},
            },
        ),
    )

    coordinator = AgentCallCoordinator(
        endpoint_resolver=AsyncMock(),
        resume_use_case=AsyncMock(),
        http_client=AsyncMock(spec=httpx.AsyncClient),
    )

    call = coordinator._extract_agent_call(output)

    assert call.correlation_id == "corr-9"
    assert call.reply_queue == "parent.reply"
    assert call.session_id == "sess-9"
    assert call.context_snapshot == {"shared_state": {"x": 1}}


async def test_coordinator_preserves_structured_response():
    """Business-critical data (file_id, content) must flow through VERBATIM."""
    sub_agent_response = {
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
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="done", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://file-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = sub_agent_response
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    resume_input = mock_resume_uc.execute.call_args[0][0]
    assert resume_input.resume_value == sub_agent_response


async def test_coordinator_handles_sub_agent_timeout():
    """When sub-agent times out, coordinator returns a terminal error without resuming."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="resumed after error", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://sub-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error == SUB_AGENT_TRANSPORT_ERROR_MESSAGE
    assert "connection refused" not in result.error


async def test_coordinator_max_depth_protection():
    """Coordinator stops at max_chain_depth to prevent infinite loops."""
    # Always returns an AGENT_CALL interrupt — never completes
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(
            status="interrupted",
            interrupted=True,
            interrupt_payload=HitlInterruptPayload(
                thread_id="thread-1",
                interrupt_id="int-2",
                value={
                    "type": "AGENT_CALL",
                    "agent_id": "looping-agent",
                    "input": "{}",
                },
            ),
        )
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://looping-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": "loop", "status": "success"}
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
        max_chain_depth=3,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    assert result.status == "error"
    assert result.error_code == "AGENT_CALL_CHAIN_LIMIT"
    assert mock_resume_uc.execute.call_count == 3


async def test_coordinator_nested_agent_calls():
    """When resumed graph triggers another AGENT_CALL, coordinator handles the chain."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_make_interrupted_output(agent_id="agent-A")
    )

    agent_b_interrupt = ResumeAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id="thread-1",
            interrupt_id="int-2",
            value={"type": "AGENT_CALL", "agent_id": "agent-B", "input": "{}"},
        ),
    )
    final_output = ResumeAgentOutput(message="all done", status="success")

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(side_effect=[agent_b_interrupt, final_output])

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": "agent result", "status": "success"}
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    assert mock_client.post.call_count == 2
    assert mock_resume_uc.execute.call_count == 2
    assert result.status == "success"
    assert result.message == "all done"


async def test_coordinator_passes_through_non_agent_call_interrupt():
    """Non-AGENT_CALL interrupts are returned as-is without calling sub-agent."""
    hitl_interrupt = ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id="thread-1",
            interrupt_id="int-1",
            value={"type": "CONFIRMATION", "message": "Please confirm"},
        ),
    )

    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=hitl_interrupt)

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resolver = AsyncMock()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_client.post.assert_not_called()
    mock_resume_uc.execute.assert_not_called()
    assert result.interrupted is True
    assert result.status == "interrupted"


async def test_coordinator_calls_correct_endpoint():
    """Coordinator uses IAgentEndpointResolver to resolve sub-agent URL."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_make_interrupted_output(agent_id="my-agent-id")
    )

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="ok", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://my-agent.svc:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "success"}
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_resolver.resolve_endpoint.assert_called_once_with("my-agent-id")
    mock_client.post.assert_called_once()
    call_url = mock_client.post.call_args[0][0]
    assert call_url == "http://my-agent.svc:8080/api/v1/execute"


async def test_agent_call_coordinator_posts_once_to_execute_endpoint():
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_make_interrupted_output(agent_id="my-agent-id")
    )

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="ok", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(
        return_value="http://my-agent.svc:8080/api/v1/execute"
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "success"}
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_client.post.assert_called_once()
    call_url = mock_client.post.call_args[0][0]
    assert call_url == "http://my-agent.svc:8080/api/v1/execute"


async def test_extract_agent_call_raises_on_non_dict_input_payload():
    """_extract_agent_call raises ValueError when input_payload is not a dict after extraction."""
    non_dict_input = "just a plain string, not JSON object"
    interrupted_output = ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id="thread-1",
            interrupt_id="int-1",
            value={
                "type": "AGENT_CALL",
                "agent_id": "sub-agent-1",
                "input": non_dict_input,
            },
        ),
    )

    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=interrupted_output)

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resolver = AsyncMock()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    with pytest.raises(ValueError, match="input_payload must be a dict"):
        await coordinator.execute_with_auto_resume(
            mock_execute_uc, ExecuteAgentInput(message="test")
        )


async def test_coordinator_json_string_input_parsed():
    """When interrupt value.input is a JSON string, it is parsed to dict for sub-agent call."""
    input_json_str = '{"message": "hello", "parameters": {"key": "val"}}'
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(
        return_value=_make_interrupted_output(input_data=input_json_str)
    )

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="ok", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "success"}
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    call_kwargs = mock_client.post.call_args[1]
    posted_json = (
        call_kwargs.get("json") or mock_client.post.call_args[0][1]
        if len(mock_client.post.call_args[0]) > 1
        else call_kwargs.get("json")
    )
    assert isinstance(posted_json, dict)
    assert posted_json == {"message": "hello", "parameters": {"key": "val"}}


async def test_call_sub_agent_returns_error_on_schema_validation_failure():
    """When input_payload violates input_schema, _call_sub_agent returns an error dict without calling the sub-agent."""
    from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability

    schema_requiring_string_name = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name"],
    }
    cap = AgentCapability(
        agent_type="strict-agent",
        name="Strict Agent",
        description="Requires name as string",
        input_schema=schema_requiring_string_name,
        output_schema={},
        required_parameters=["name"],
    )

    payload_violating_schema = {"name": 42}

    interrupted_output = ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id="thread-1",
            interrupt_id="int-1",
            value={
                "type": "AGENT_CALL",
                "agent_id": "strict-agent-id",
                "agent_type": "strict-agent",
                "input": payload_violating_schema,
            },
        ),
    )

    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=interrupted_output)

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="resumed", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://strict-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
        capabilities={"strict-agent": cap},
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_client.post.assert_not_called()
    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error is not None
    assert "schema" in result.error.lower()


async def test_call_sub_agent_returns_error_on_malformed_response():
    """When sub-agent returns a response with invalid field types, coordinator returns terminal error."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="resumed", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://sub-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message": "ok",
        "status": "success",
        "duration_ms": "not-a-number",
    }
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error is not None
    assert "malformed" in result.error.lower() or "invalid" in result.error.lower()


async def test_coordinator_returns_terminal_error_on_connect_error():
    """When sub-agent is unreachable (ConnectError), coordinator returns terminal error."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://dead-host:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error is not None


async def test_coordinator_returns_terminal_error_on_missing_required_parameters():
    """When required params are missing, coordinator returns terminal error without calling sub-agent."""
    from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability

    cap = AgentCapability(
        agent_type="strict-agent",
        name="Strict Agent",
        description="Requires file_id param",
        input_schema={},
        output_schema={},
        required_parameters=["file_id"],
    )

    interrupted_output = ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=HitlInterruptPayload(
            thread_id="thread-1",
            interrupt_id="int-1",
            value={
                "type": "AGENT_CALL",
                "agent_id": "strict-agent-id",
                "agent_type": "strict-agent",
                "input": {"message": "no file_id here"},
            },
        ),
    )

    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=interrupted_output)

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://strict-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
        capabilities={"strict-agent": cap},
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_client.post.assert_not_called()
    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error is not None


async def test_coordinator_returns_terminal_error_on_retry_exhaustion():
    """When sub-agent returns retryable status codes and retries are exhausted, coordinator returns terminal error."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://flaky-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
        max_retries=1,
        retry_backoff_seconds=0.0,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error is not None


async def test_coordinator_returns_terminal_error_on_non_retryable_http_status():
    """Non-retryable HTTP error (e.g., 404) from sub-agent causes terminal coordinator error."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://sub-agent:8080")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=mock_response,
        )
    )
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_resume_uc.execute.assert_not_called()
    assert result.status == "error"
    assert result.error_code == "SUB_AGENT_TRANSPORT_ERROR"
    assert result.error is not None


async def test_coordinator_resumes_with_genuine_business_error_without_sentinel():
    """Sub-agent business error payloads (success=False, no _coordinator_error) still flow to resume."""
    mock_execute_uc = AsyncMock()
    mock_execute_uc.execute = AsyncMock(return_value=_make_interrupted_output())

    mock_resume_uc = AsyncMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=ResumeAgentOutput(message="handled error", status="success")
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://sub-agent:8080")

    business_error_payload = {
        "message": "validation failed",
        "status": "error",
        "success": False,
        "error": "Required field missing",
        "error_code": "VALIDATION_ERROR",
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock()
    mock_response.json.return_value = business_error_payload
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_client,
    )

    result = await coordinator.execute_with_auto_resume(
        mock_execute_uc, ExecuteAgentInput(message="test")
    )

    mock_resume_uc.execute.assert_called_once()
    resume_input = mock_resume_uc.execute.call_args[0][0]
    assert resume_input.resume_value == business_error_payload
    assert result.status == "success"
