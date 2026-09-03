from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from tests.helpers.testing import StubLogger, StubMonitor


@pytest.mark.asyncio
async def test_execute_rejects_invalid_main_conversation_metadata_without_invoking_graph():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    graph = MagicMock()
    graph.ainvoke = AsyncMock()
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=graph
    )

    result = await use_case.execute(
        ExecuteAgentInput(
            message="hello",
            conv_id="main_bad_1",
            metadata=ConversationMetadata(main_conv_id="main_other_1"),
        )
    )

    assert result.status == "error"
    assert result.error_code == "INVALID_CONVERSATION_METADATA"
    graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_execute_rejects_uploaded_file_mismatch_without_invoking_graph():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    graph = MagicMock()
    graph.ainvoke = AsyncMock()
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=graph
    )

    result = await use_case.execute(
        ExecuteAgentInput(
            message="hello",
            conv_id="main_files_1",
            uploaded_file_ids=["file-a"],
            metadata=ConversationMetadata(
                main_conv_id="main_files_1",
                uploaded_file_ids=["file-b"],
            ),
        )
    )

    assert result.status == "error"
    assert result.error_code == "INVALID_CONVERSATION_METADATA"
    graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_execute_backfills_uploaded_files_from_top_level_to_metadata_and_allows_legacy_conversation_ids():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"formatted_response": {"content": "ok", "status": "success"}}
    )
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=graph
    )

    request = ExecuteAgentInput(
        message="hello",
        conv_id="legacy-conv-1",
        uploaded_file_ids=["file-legacy-1"],
    )
    result = await use_case.execute(request)

    assert result.status == "success"
    initial_state = graph.ainvoke.call_args.args[0]
    assert initial_state["uploaded_file_ids"] == ["file-legacy-1"]
    assert initial_state["metadata"]["uploaded_file_ids"] == ["file-legacy-1"]


@pytest.mark.asyncio
async def test_resume_rejects_invalid_sub_conversation_metadata_without_invoking_runtime():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    runtime = MagicMock()
    runtime.resume = AsyncMock()
    use_case = ResumeAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_runtime=runtime
    )

    result = await use_case.execute(
        ResumeAgentInput(
            thread_id="sub_missing_1",
            resume_value="approved",
            metadata=ConversationMetadata(
                main_conv_id="main_resume_1",
                sub_conv_ids=["sub_other_1"],
            ),
        )
    )

    assert result.status == "error"
    assert result.error_code == "INVALID_CONVERSATION_METADATA"
    runtime.resume.assert_not_called()
