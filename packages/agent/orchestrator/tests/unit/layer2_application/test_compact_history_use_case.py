"""SA-1948 — orchestrator compacts the stored full conversation history (CONTEXT column)
by reusing the agent-sdk ContextBudgetManager (TIERED), token-triggered per turn.

Only writes back when a real ``[Conversation summary]`` was produced (over budget AND
longer than the kept tail); preserves the summarized turns' tasks/files on the summary
entry; emits chat:thinking when it compacts; no-op when the manager is disabled."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    Conversation,
    GlobalContext,
    Metadata,
)
from agent_sdk.layer1_domain.entities.context_config import (
    CompactionStrategy,
    ContextBudgetConfig,
)
from agent_sdk.layer1_domain.entities.conversation_context import (
    FileObject,
    KnowledgeItem,
    MessageHistory,
    TaskHistory,
)
from agent_sdk.layer4_frameworks.ai.context_budget import ContextBudgetManager
from app.layer2_application.features.orchestrate_graph.use_cases import (
    orchestrate_graph_use_case as module,
)

KEEP = 2


def _summarizing_manager() -> ContextBudgetManager:
    """Real engine wired so any over-budget history with > KEEP turns is summarized
    (aggressive gate low, danger gate effectively disabled → always the LLM path)."""
    mock_chat_client = MagicMock()
    mock_chat_client.ainvoke = AsyncMock(
        return_value=MagicMock(content="user discussed several things earlier.")
    )
    mock_llm = MagicMock()
    mock_llm.get_chat_client.return_value = mock_chat_client
    config = ContextBudgetConfig(
        max_total_tokens=100,
        max_message_history=KEEP,
        compaction_threshold=0.3,
        aggressive_threshold=0.3,
        danger_threshold=10.0,
        compaction_strategy=CompactionStrategy.TIERED,
    )
    return ContextBudgetManager(config, llm_service=mock_llm)


def _message(message_id: str, text: str, *, task_id: str | None = None) -> MessageHistory:
    tasks = (
        [TaskHistory(task_id=task_id, knowledge=[KnowledgeItem(document_id="d", summary="s")])]
        if task_id
        else []
    )
    files = [FileObject(file_id=f"file-{message_id}")] if task_id else []
    return MessageHistory(message_id=message_id, message=text, tasks=tasks, files=files)


def _conversation(history: list[MessageHistory], conv_id: str = "conv-1") -> Conversation:
    return Conversation(
        conv_id=conv_id,
        user_id="user-1",
        shared_state=AgentSharedState(
            metadata=Metadata(conv_id=conv_id, checkpoint_id="CP-01", current_message_id="m"),
            context=GlobalContext(conversation_context=history, knowledge_base_id="kb-9"),
        ),
    )


def _build_use_case(*, context_budget_manager, orchestration_state_repo):
    uuid_builder = MagicMock()
    uuid_builder.create_uuid7.return_value = "summary-id"
    settings = MagicMock()
    settings.CONTEXT_MAX_MESSAGE_HISTORY = KEEP
    # The inline per-turn gate reads these (must match the manager's config → gate = 100 * 0.3 = 30).
    settings.CONTEXT_MAX_TOTAL_TOKENS = 100
    settings.CONTEXT_COMPACTION_THRESHOLD = 0.3
    return module.OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=MagicMock(),
        push_gateway_notifier=MagicMock(send_chat_thinking=AsyncMock()),
        agent_registry_client=MagicMock(),
        workflow_client=MagicMock(),
        llm_provider=MagicMock(),
        orchestration_state_repo=orchestration_state_repo,
        agent_event_repo=MagicMock(),
        publisher=MagicMock(),
        response_formatter=MagicMock(),
        uuid_builder=uuid_builder,
        settings=settings,
        context_budget_manager=context_budget_manager,
    )


@pytest.mark.asyncio
async def test_compacts_and_writes_summary_when_over_budget_and_long():
    # 6 long turns → over budget AND longer than the kept tail → real summary produced.
    history = [
        _message("m0", "word " * 20, task_id="t0"),
        _message("m1", "word " * 20, task_id="t1"),
        _message("m2", "word " * 20),
        _message("m3", "word " * 20),
        _message("m4", "recent one " * 3),
        _message("m5", "recent two " * 3),
    ]
    repo = MagicMock(
        get_conversation_by=MagicMock(return_value=_conversation(history)),
        update_context=MagicMock(),
    )
    use_case = _build_use_case(context_budget_manager=_summarizing_manager(), orchestration_state_repo=repo)

    await use_case.compact_history("conv-1")

    # chat:thinking emitted before summarizing, now enriched with the token count + reason.
    use_case.push_gateway_notifier.send_chat_thinking.assert_awaited_once()
    thinking = use_case.push_gateway_notifier.send_chat_thinking.await_args.kwargs["content"]
    assert thinking.startswith("Compacting the conversation")
    assert "tokens" in thinking and "summarizing" in thinking  # friendly reason + token detail

    # Wrote back exactly once, only the CONTEXT column (via update_context).
    repo.update_context.assert_called_once()
    written_conv_id, written_ctx = repo.update_context.call_args.args
    assert written_conv_id == "conv-1"
    new_history = written_ctx.conversation_context
    # [summary] + last KEEP originals.
    assert len(new_history) == KEEP + 1
    assert new_history[0].message.startswith("[Conversation summary]")
    assert new_history[1:] == history[-KEEP:]
    # Summary entry aggregates the summarized-away turns' tasks/files → EXECUTIONS link kept.
    assert [t.task_id for t in new_history[0].tasks] == ["t0", "t1"]
    assert len(new_history[0].files) == 2
    # knowledge_base_id preserved.
    assert written_ctx.knowledge_base_id == "kb-9"


@pytest.mark.asyncio
async def test_no_write_when_under_token_threshold():
    history = [_message("m0", "hi"), _message("m1", "there")]
    repo = MagicMock(
        get_conversation_by=MagicMock(return_value=_conversation(history)),
        update_context=MagicMock(),
    )
    use_case = _build_use_case(context_budget_manager=_summarizing_manager(), orchestration_state_repo=repo)

    await use_case.compact_history("conv-1")

    repo.update_context.assert_not_called()
    use_case.push_gateway_notifier.send_chat_thinking.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_write_when_over_budget_but_within_kept_tail():
    # Exactly KEEP long turns → over budget, but engine keeps all (no summary) → no write.
    history = [_message("m0", "word " * 25), _message("m1", "word " * 25)]
    repo = MagicMock(
        get_conversation_by=MagicMock(return_value=_conversation(history)),
        update_context=MagicMock(),
    )
    use_case = _build_use_case(context_budget_manager=_summarizing_manager(), orchestration_state_repo=repo)

    await use_case.compact_history("conv-1")

    repo.update_context.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_manager_disabled():
    repo = MagicMock(get_conversation_by=MagicMock(), update_context=MagicMock())
    use_case = _build_use_case(context_budget_manager=None, orchestration_state_repo=repo)

    await use_case.compact_history("conv-1")

    repo.get_conversation_by.assert_not_called()
    repo.update_context.assert_not_called()
