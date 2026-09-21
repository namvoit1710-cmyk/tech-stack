import asyncio
import dataclasses
from unittest.mock import MagicMock

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer2_application.services.workflow_event_runtime import (
    _get_active_scope as get_active_scope,
)
from agent_sdk.layer2_application.services.workflow_event_runtime import (
    workflow_event_scope,
)


async def test_scope_available_in_direct_await():
    """Scope must be available when using direct await (current production pattern)."""
    mock_emitter = MagicMock()
    with workflow_event_scope(mock_emitter, mode="execute"):
        scope = get_active_scope()
        assert scope is not None
        assert scope["emitter"] == mock_emitter


async def test_scope_propagation_to_create_task():
    """Test if scope propagates to asyncio.create_task (LangGraph parallel nodes)."""
    mock_emitter = MagicMock()
    with workflow_event_scope(mock_emitter, mode="execute"):

        async def child_coroutine():
            return get_active_scope()

        # asyncio.create_task copies context in Python 3.7+
        task = asyncio.create_task(child_coroutine())
        child_scope = await task

        # This should PASS per Python 3.7+ ContextVar semantics
        assert child_scope is not None, "ContextVar not propagated to child task"
        assert child_scope["emitter"] == mock_emitter


async def test_scope_not_available_outside_context():
    """After exiting the context manager, scope must be None."""
    mock_emitter = MagicMock()
    with workflow_event_scope(mock_emitter, mode="execute"):
        pass
    assert get_active_scope() is None


async def test_scope_derives_execute_request_metadata_context():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )

    request = ExecuteAgentInput(
        message="hello",
        conv_id="main_scope_1",
        correlation_id="corr-scope-1",
        reply_to="agent.reply",
        metadata=ConversationMetadata(
            main_conv_id="main_scope_1",
            sub_conv_ids=["sub_scope_1"],
            uploaded_file_ids=["file-1"],
        ),
        uploaded_file_ids=["file-1"],
    )

    with workflow_event_scope(MagicMock(), request=request, mode="execute"):
        scope = get_active_scope()

    assert scope is not None
    assert scope["conv_id"] == "main_scope_1"
    assert scope["correlation_id"] == "corr-scope-1"
    assert scope["reply_to"] == "agent.reply"
    assert scope["metadata"] == dataclasses.asdict(request.metadata)
    assert scope["uploaded_file_ids"] == ["file-1"]


async def test_scope_derives_resume_request_metadata_context():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
    )

    request = ResumeAgentInput(
        thread_id="sub_scope_2",
        resume_value="approved",
        correlation_id="corr-scope-2",
        metadata=ConversationMetadata(
            main_conv_id="main_scope_2",
            sub_conv_ids=["sub_scope_2"],
            uploaded_file_ids=["file-2"],
        ),
        uploaded_file_ids=["file-2"],
    )

    with workflow_event_scope(MagicMock(), request=request, mode="resume"):
        scope = get_active_scope()

    assert scope is not None
    assert scope["conv_id"] == "sub_scope_2"
    assert scope["correlation_id"] == "corr-scope-2"
    assert scope["metadata"] == dataclasses.asdict(request.metadata)
    assert scope["uploaded_file_ids"] == ["file-2"]
