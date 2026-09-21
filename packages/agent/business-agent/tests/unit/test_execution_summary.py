import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from agent_sdk import settings
from agent_sdk.layer1_domain.entities.agent_shared_state import (
    GlobalContext,
    Metadata,
    Task,
    TaskStatus,
)
from agent_sdk.layer1_domain.entities.conversation_context import (
    KnowledgeItem,
    MessageHistory,
    TaskHistory,
)
from app.layer1_domain.entities.next_step_suggestion import (
    NextStepKind,
    NextStepSuggestion,
)
from app.layer1_domain.entities.planner import PlannerResponse, PlannerResponseType
from app.layer1_domain.entities.planning_context import (
    ExecutionSummaryContext,
    PlanningContextType,
)
from app.layer2_application.features.main_agent.use_cases.create_plan_usecase import (
    CreatePlanUseCase,
)
from app.layer4_frameworks.planners.llm_execution_summary import (
    LLMExecutionSummaryGenerator,
)
from app.layer4_frameworks.planners.planner_resolver import PlannerResolver


# -----------------------------
# Fixtures / builders
# -----------------------------
def _metadata(**updates: Any) -> Metadata:
    payload: dict[str, Any] = {
        "conv_id": "conv-1",
        "checkpoint_id": "cp-1",
        "current_task_id": "task-1",
        "current_message_id": "msg-1",
    }
    payload.update(updates)
    return Metadata(**payload)


def _task(
    *,
    task_id: str = "task-1",
    goal: str = "Fetch revenue",
    status: TaskStatus = TaskStatus.SUCCESS,
    output_data: dict[str, Any] | None = None,
    error_detail: str | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        agent="tool.report",
        name=goal,
        status=status,
        output_data=output_data,
        error_detail=error_detail,
    )


def _message(
    message_id: str = "msg-1",
    message: str = "What is revenue?",
    task_ids: list[str] | None = None,
) -> MessageHistory:
    tasks = [TaskHistory(task_id=tid, knowledge=[]) for tid in (task_ids or ["task-1"])]
    return MessageHistory(message_id=message_id, message=message, tasks=tasks, files=[])


def _global_context(*messages: MessageHistory) -> GlobalContext:
    return GlobalContext(conversation_context=list(messages) or [_message()])


def _summary_context() -> ExecutionSummaryContext:
    return ExecutionSummaryContext(
        current_message=_message(),
        executions=[_task(output_data={"total": 42})],
        global_context=_global_context(),
        metadata=_metadata(),
    )


def _usecase(
    *,
    planner_resolver: Any = None,
    agent_shared_state_repository: Any = None,
    push_gateway_notifier: Any = None,
) -> CreatePlanUseCase:
    planner_resolver = planner_resolver or SimpleNamespace(resolve=Mock())
    agent_shared_state_repository = agent_shared_state_repository or SimpleNamespace(
        update_metadata=Mock(),
        get_context_by_conv_id=Mock(return_value=_global_context()),
        get_executions_by_message_id=Mock(return_value=[_task()]),
    )
    push_gateway_notifier = push_gateway_notifier or SimpleNamespace(
        send_task_summary_event=AsyncMock(),
    )

    return CreatePlanUseCase(
        planner_resolver=cast(Any, planner_resolver),
        agent_hub_client=cast(Any, SimpleNamespace()),
        agent_shared_state_repository=cast(Any, agent_shared_state_repository),
        workflow_client=cast(Any, SimpleNamespace()),
        push_gateway_notifier=cast(Any, push_gateway_notifier),
        logger=cast(Any, SimpleNamespace(info=Mock(), error=Mock(), warning=Mock(), debug=Mock())),
        publisher=cast(Any, SimpleNamespace(publish=AsyncMock())),
        uuid_builder=cast(Any, SimpleNamespace(create_uuid7=Mock(return_value="uuid-1"))),
        settings=settings,
        file_client=cast(Any, SimpleNamespace()),
    )


# -----------------------------
# Resolver wiring
# -----------------------------
def test_planner_resolver_returns_execution_summary_generator() -> None:
    resolver = PlannerResolver(llm=cast(Any, object()))

    generator = resolver.resolve(PlanningContextType.LLM_EXECUTION_SUMMARY)

    assert isinstance(generator, LLMExecutionSummaryGenerator)


# -----------------------------
# Generator
# -----------------------------
@pytest.mark.asyncio
async def test_execution_summary_generator_calls_llm_and_parses() -> None:
    llm = SimpleNamespace(
        get_chat_completion=AsyncMock(
            return_value=json.dumps({"type": "summary", "answer": "All done", "steps": []})
        )
    )
    generator = LLMExecutionSummaryGenerator(cast(Any, llm))

    result = await generator.generate(_summary_context())

    assert result.type == PlannerResponseType.SUMMARY
    assert result.answer == "All done"
    call = llm.get_chat_completion.await_args
    assert call.args[1] == ""
    assert call.kwargs["json_mode"] is True
    prompt = call.args[0]
    assert "What is revenue?" in prompt
    assert "Fetch revenue" in prompt


@pytest.mark.asyncio
async def test_execution_summary_prompt_includes_gathered_knowledge() -> None:
    llm = SimpleNamespace(
        get_chat_completion=AsyncMock(
            return_value=json.dumps({"type": "summary", "answer": "ok", "steps": []})
        )
    )
    generator = LLMExecutionSummaryGenerator(cast(Any, llm))
    message = MessageHistory(
        message_id="msg-1",
        message="approve change request",
        tasks=[
            TaskHistory(
                task_id="task-1",
                knowledge=[
                    KnowledgeItem(document_id="doc-1", summary="Change request CR-42 approved."),
                ],
            )
        ],
        files=[],
    )
    context = ExecutionSummaryContext(
        current_message=message,
        executions=[_task()],
        global_context=_global_context(message),
        metadata=_metadata(),
    )

    await generator.generate(context)

    prompt = llm.get_chat_completion.await_args.args[0]
    assert "Change request CR-42 approved." in prompt
    assert "Gathered knowledge" in prompt


# -----------------------------
# _build_execution_summary_context
# -----------------------------
@pytest.mark.asyncio
async def test_build_execution_summary_context_selects_current_message() -> None:
    current = _message(message_id="msg-1", message="current message")
    global_context = _global_context(_message(message_id="msg-0"), current)
    scoped = [_task(task_id="task-1"), _task(task_id="task-2")]
    repository = SimpleNamespace(
        update_metadata=Mock(),
        get_context_by_conv_id=Mock(return_value=global_context),
        get_executions_by_message_id=Mock(return_value=scoped),
    )
    subject = _usecase(agent_shared_state_repository=repository)
    metadata = _metadata(current_message_id="msg-1")

    context = await subject._build_execution_summary_context(metadata)

    assert context.current_message.message_id == "msg-1"
    # executions are scoped to the current message by the repository (SQL layer)
    repository.get_executions_by_message_id.assert_called_once_with("conv-1", "msg-1")
    assert context.executions == scoped
    assert context.global_context is global_context
    assert context.metadata is metadata


@pytest.mark.asyncio
async def test_build_execution_summary_context_requires_global_context() -> None:
    repository = SimpleNamespace(
        update_metadata=Mock(),
        get_context_by_conv_id=Mock(return_value=None),
        get_executions_by_message_id=Mock(return_value=[]),
    )
    subject = _usecase(agent_shared_state_repository=repository)

    with pytest.raises(ValueError, match="Global context not found"):
        await subject._build_execution_summary_context(_metadata())


@pytest.mark.asyncio
async def test_build_execution_summary_context_requires_current_message() -> None:
    repository = SimpleNamespace(
        update_metadata=Mock(),
        get_context_by_conv_id=Mock(return_value=_global_context(_message("msg-0"))),
        get_executions_by_message_id=Mock(return_value=[]),
    )
    subject = _usecase(agent_shared_state_repository=repository)

    with pytest.raises(ValueError, match="Current message not found"):
        await subject._build_execution_summary_context(_metadata(current_message_id="missing"))


# -----------------------------
# _finalize_workflow
# -----------------------------
@pytest.mark.asyncio
async def test_finalize_workflow_sends_execution_summary() -> None:
    planner = SimpleNamespace(
        generate=AsyncMock(
            return_value=PlannerResponse(type=PlannerResponseType.SUMMARY, answer="Summary text")
        )
    )
    resolver = SimpleNamespace(resolve=Mock(return_value=planner))
    repository = SimpleNamespace(
        update_metadata=Mock(),
        get_context_by_conv_id=Mock(return_value=_global_context()),
        get_executions_by_message_id=Mock(return_value=[_task()]),
    )
    notifier = SimpleNamespace(send_task_summary_event=AsyncMock())
    subject = _usecase(
        planner_resolver=resolver,
        agent_shared_state_repository=repository,
        push_gateway_notifier=notifier,
    )
    metadata = _metadata(current_task_id="task-1")

    await subject._finalize_workflow(metadata)

    resolver.resolve.assert_called_once_with(PlanningContextType.LLM_EXECUTION_SUMMARY)
    context = planner.generate.await_args.args[0]
    assert isinstance(context, ExecutionSummaryContext)
    notifier.send_task_summary_event.assert_awaited_once_with(
        conv_id="conv-1", summary="Summary text", suggestions=[]
    )
    assert metadata.current_task_id == ""
    repository.update_metadata.assert_called_once_with(conv_id="conv-1", metadata=metadata)


@pytest.mark.asyncio
async def test_finalize_workflow_forwards_next_step_suggestions() -> None:
    suggestions = [
        NextStepSuggestion(title="Review report", prompt="Open the revenue report", kind=NextStepKind.FOLLOW_UP),
    ]
    planner = SimpleNamespace(
        generate=AsyncMock(
            return_value=PlannerResponse(
                type=PlannerResponseType.SUMMARY,
                answer="Summary text",
                suggestions=suggestions,
            )
        )
    )
    resolver = SimpleNamespace(resolve=Mock(return_value=planner))
    repository = SimpleNamespace(
        update_metadata=Mock(),
        get_context_by_conv_id=Mock(return_value=_global_context()),
        get_executions_by_message_id=Mock(return_value=[_task()]),
    )
    notifier = SimpleNamespace(send_task_summary_event=AsyncMock())
    subject = _usecase(
        planner_resolver=resolver,
        agent_shared_state_repository=repository,
        push_gateway_notifier=notifier,
    )

    await subject._finalize_workflow(_metadata(current_task_id="task-1"))

    notifier.send_task_summary_event.assert_awaited_once_with(
        conv_id="conv-1", summary="Summary text", suggestions=suggestions
    )


@pytest.mark.asyncio
async def test_execution_summary_prompt_requests_next_steps() -> None:
    llm = SimpleNamespace(
        get_chat_completion=AsyncMock(
            return_value=json.dumps({"type": "summary", "answer": "ok", "steps": [], "next_steps": []})
        )
    )
    generator = LLMExecutionSummaryGenerator(cast(Any, llm))

    result = await generator.generate(_summary_context())

    assert result.suggestions == []
    prompt = llm.get_chat_completion.await_args.args[0]
    assert "next_steps" in prompt


@pytest.mark.asyncio
async def test_finalize_workflow_logs_suggestion_count() -> None:
    suggestions = [
        NextStepSuggestion(title="Review report", prompt="Open the revenue report"),
        NextStepSuggestion(title="Approve", prompt="Approve the request"),
    ]
    planner = SimpleNamespace(
        generate=AsyncMock(
            return_value=PlannerResponse(
                type=PlannerResponseType.SUMMARY,
                answer="Done",
                suggestions=suggestions,
            )
        )
    )
    resolver = SimpleNamespace(resolve=Mock(return_value=planner))
    logger_mock = SimpleNamespace(info=Mock(), error=Mock(), warning=Mock(), debug=Mock())
    subject = _usecase(planner_resolver=resolver)
    subject.logger = logger_mock

    await subject._finalize_workflow(_metadata())

    logger_mock.info.assert_called_once_with(
        "Generated next-step suggestions after task completion",
        conv_id="conv-1",
        suggestion_count=2,
    )


@pytest.mark.asyncio
async def test_finalize_workflow_skips_summary_when_answer_missing() -> None:
    planner = SimpleNamespace(
        generate=AsyncMock(return_value=PlannerResponse(type=PlannerResponseType.SUMMARY, answer=None))
    )
    resolver = SimpleNamespace(resolve=Mock(return_value=planner))
    notifier = SimpleNamespace(send_task_summary_event=AsyncMock())
    subject = _usecase(planner_resolver=resolver, push_gateway_notifier=notifier)

    await subject._finalize_workflow(_metadata())

    notifier.send_task_summary_event.assert_not_awaited()


# -----------------------------
# _mark_run_output_completed
# -----------------------------
def _workflow_event(run_id: str, root_run_id: str | None = None) -> Any:
    from app.layer1_domain.entities.workflow_event import WorkflowEvent, WorkflowStatus

    return WorkflowEvent(
        event_id="evt-1",
        timestamp="2026-07-07T02:55:00+00:00",
        source="workflow",
        root_run_id=root_run_id or run_id,
        depth=0,
        run_id=run_id,
        status=WorkflowStatus.COMPLETED,
    )


def _run_task(run_id: str, *, status: str = "RUNNING") -> Task:
    task = _task(
        task_id="task-start",
        goal="Start the approval process",
        status=TaskStatus.SUCCESS,
        output_data={"id": run_id, "status": status, "completed_at": None},
    )
    task.input_data = {"predefined_run_id": run_id, "execution_mode": "auto"}
    return task


def test_mark_run_output_completed_refreshes_started_run_output() -> None:
    run_id = "run-42"
    started = _run_task(run_id)
    repository = SimpleNamespace(update_task=Mock())
    subject = _usecase(agent_shared_state_repository=repository)

    subject._mark_run_output_completed(
        "conv-1", _metadata(current_run_id=run_id), started, _workflow_event(run_id)
    )

    repository.update_task.assert_called_once()
    kwargs = repository.update_task.call_args.kwargs
    assert kwargs["task_id"] == "task-start"
    assert kwargs["output_data"]["status"] == "success"
    assert kwargs["output_data"]["completed_at"] == "2026-07-07T02:55:00+00:00"
    # the in-memory task is kept in sync too
    assert started.output_data["status"] == "success"


def test_mark_run_output_completed_noop_when_run_id_mismatch() -> None:
    started = _run_task("run-42")
    repository = SimpleNamespace(update_task=Mock())
    subject = _usecase(agent_shared_state_repository=repository)

    subject._mark_run_output_completed(
        "conv-1", _metadata(current_run_id="run-other"), started, _workflow_event("run-other")
    )

    repository.update_task.assert_not_called()
    assert started.output_data["status"] == "RUNNING"


def test_mark_run_output_completed_noop_when_no_current_run_id() -> None:
    started = _run_task("run-42")
    repository = SimpleNamespace(update_task=Mock())
    subject = _usecase(agent_shared_state_repository=repository)

    subject._mark_run_output_completed(
        "conv-1", _metadata(current_run_id=None), started, _workflow_event("run-42")
    )

    repository.update_task.assert_not_called()
