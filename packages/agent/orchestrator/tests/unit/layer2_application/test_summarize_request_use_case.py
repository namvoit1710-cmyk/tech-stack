from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.guardrail.input_guard import InputGuardResult
from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
from app.layer1_domain.entities.orchestrator.ui_event import UIEventRole, UIEventType
from app.layer1_domain.entities.classifier.conversation_summary_gate import GateDecision, SummaryIntent
from app.layer1_domain.entities.planner.conversation_summarizer import ConversationSummaryResponse
from app.layer2_application.features.orchestrate_graph.use_cases import (
    orchestrate_graph_use_case as module,
)
from app.layer2_application.features.orchestrate_graph.use_cases.orchestrate_graph_use_case import (
    OrchestrateGraphUseCase,
    OrchestrationGraphCommand,
)


def _event(role, content, event_type=UIEventType.CHAT_RESPONSE.value):
    return AgentEvent(
        event_id=f"evt-{content[:6]}",
        role=role,
        event_type=event_type,
        conv_id="conv-1",
        payload={"content": content},
    )


def _build_use_case(*, agent_event_repo, llm_provider, push_gateway_notifier):
    uuid_builder = MagicMock()
    uuid_builder.create_uuid7.side_effect = [f"uuid-{i}" for i in range(20)]

    input_guard = MagicMock()
    input_guard.check.return_value = InputGuardResult(passed=True, sanitized_message="ok")

    orchestration_state_repo = MagicMock()
    orchestration_state_repo.get_conversation_by.return_value = None  # main conversation

    return OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=input_guard,
        push_gateway_notifier=push_gateway_notifier,
        agent_registry_client=MagicMock(),
        workflow_client=MagicMock(),
        llm_provider=llm_provider,
        orchestration_state_repo=orchestration_state_repo,
        agent_event_repo=agent_event_repo,
        publisher=MagicMock(publish=AsyncMock()),
        response_formatter=MagicMock(),
        uuid_builder=uuid_builder,
        settings=MagicMock(),
    )


@pytest.mark.asyncio
async def test_summarize_request_short_circuits_and_emits_summary(monkeypatch):
    sent = []

    async def _capture(*, push_gateway_notifier, event_id, conv_id, content):
        sent.append(content)

    monkeypatch.setattr(module, "send_chat_response_event", _capture)
    monkeypatch.setattr(module, "send_chat_thinking_event", AsyncMock())

    agent_event_repo = MagicMock()
    agent_event_repo.get_events_by_conversation_id.return_value = [
        _event(UIEventRole.USER, "Create a material master for product X"),
        _event(UIEventRole.ASSISTANT, "Created draft change request CR-42."),
        _event(UIEventRole.ASSISTANT, "thinking...", event_type=UIEventType.CHAT_THINKING.value),
    ]

    llm_provider = MagicMock()
    # Two LLM calls now: (1) the Stage-0 gate classifies intent, (2) the summarizer.
    llm_provider.complete_with_format.side_effect = [
        GateDecision(intent=SummaryIntent.CONVERSATION_SUMMARY.value),
        ConversationSummaryResponse(
            summary="We created a material master.",
            what_the_system_did="Opened change request CR-42.",
            recommended_next_steps=["Review CR-42"],
        ),
    ]

    use_case = _build_use_case(
        agent_event_repo=agent_event_repo,
        llm_provider=llm_provider,
        push_gateway_notifier=MagicMock(send_notification=AsyncMock()),
    )
    classify_spy = AsyncMock()
    use_case._classify_and_build_plan = classify_spy  # type: ignore[method-assign]

    command = OrchestrationGraphCommand(
        conv_id="conv-1",
        user_id="user-1",
        message="summarize the conversation",
    )
    await use_case._trigger_graph_for_main_conversation(conv_id="conv-1", command=command)

    # Summarize path taken — planner never runs (no "Not supported").
    classify_spy.assert_not_awaited()
    # History was read and the thinking event was excluded from the transcript.
    agent_event_repo.get_events_by_conversation_id.assert_called_once_with("conv-1")
    transcript = llm_provider.complete_with_format.call_args.kwargs["messages"][-1]["content"]
    assert "CR-42" in transcript
    assert "thinking..." not in transcript
    # A summary was emitted to the user.
    assert sent and "CR-42" in sent[0]


@pytest.mark.asyncio
async def test_non_summarize_request_still_plans(monkeypatch):
    monkeypatch.setattr(module, "send_chat_thinking_event", AsyncMock())

    from app.layer1_domain.entities.planner.task_breakdown_response import TaskBreakdownResponse

    llm_provider = MagicMock()
    # Gate classifies this as ordinary business work -> planner must run.
    llm_provider.complete_with_format.return_value = GateDecision(
        intent=SummaryIntent.BUSINESS.value
    )

    use_case = _build_use_case(
        agent_event_repo=MagicMock(),
        llm_provider=llm_provider,
        push_gateway_notifier=MagicMock(send_notification=AsyncMock()),
    )
    use_case._classify_and_build_plan = AsyncMock(  # type: ignore[method-assign]
        return_value=TaskBreakdownResponse(tasks=[], reasoning="")
    )
    use_case._emit_error_response = AsyncMock()  # type: ignore[method-assign]

    command = OrchestrationGraphCommand(
        conv_id="conv-1", user_id="user-1", message="create a material master record"
    )
    await use_case._trigger_graph_for_main_conversation(conv_id="conv-1", command=command)

    use_case._classify_and_build_plan.assert_awaited_once()
