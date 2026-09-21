"""SA-2168 — /rename: set a conversation title via type=RENAME (deterministic, no LLM).

Covers the full chain: TaskType.RENAME, OrchestrateUseCase._validate_routing accepting
RENAME, OrchestrateGraphUseCase._is_rename_request / _handle_rename_request (ownership,
title sanitization, the confirmation chat:response), and that default/AGENT routing is
unaffected (no regression)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    Conversation,
    GlobalContext,
    Metadata,
)
from app.layer1_domain.entities.exceptions import PermissionDeniedError, RequestValidationAppError
from app.layer1_domain.entities.orchestrator.task_type import TaskType
from app.layer2_application.features.orchestrate.use_cases.orchestrate_use_case import (
    OrchestrateUseCase,
)
from app.layer2_application.features.orchestrate_graph.use_cases import (
    orchestrate_graph_use_case as module,
)
from app.layer2_application.features.orchestrate_graph.use_cases.dtos.orchestration_parameters import (
    OrchestrationParameters,
)
from app.layer2_application.interfaces.conversation_summary_repository import (
    ConversationTitleSource,
)


def _conversation(conv_id="conv-1", user_id="user-1"):
    return Conversation(
        conv_id=conv_id,
        user_id=user_id,
        shared_state=AgentSharedState(
            metadata=Metadata(conv_id=conv_id, checkpoint_id="CP-01", current_message_id="msg-1"),
            context=GlobalContext(),
        ),
    )


def _build_use_case(*, orchestration_state_repo=None, conversation_summary_repo=None, push_gateway_notifier=None):
    uuid_builder = MagicMock()
    uuid_builder.create_uuid7.side_effect = [f"id-{i}" for i in range(20)]
    settings = MagicMock()
    return module.OrchestrateGraphUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        permission_provider=MagicMock(),
        input_guard=MagicMock(),
        push_gateway_notifier=push_gateway_notifier or MagicMock(send_chat_response=AsyncMock()),
        agent_registry_client=MagicMock(),
        workflow_client=MagicMock(),
        llm_provider=MagicMock(),
        orchestration_state_repo=orchestration_state_repo or MagicMock(),
        agent_event_repo=MagicMock(),
        publisher=MagicMock(),
        response_formatter=MagicMock(
            format_error_from_state=MagicMock(return_value=MagicMock(content="error"))
        ),
        uuid_builder=uuid_builder,
        settings=settings,
        conversation_summary_repo=conversation_summary_repo,
    )


# ── TaskType ─────────────────────────────────────────────────────────────────

def test_task_type_from_value_resolves_rename():
    assert TaskType.from_value("RENAME") is TaskType.RENAME


def test_task_type_from_value_still_rejects_unknown_values():
    with pytest.raises(ValueError):
        TaskType.from_value("Bogus")


# ── OrchestrateUseCase._validate_routing ────────────────────────────────────

def test_validate_routing_accepts_rename_without_parameters():
    # Should not raise: RENAME needs no parameters.agent_id (unlike AGENT).
    OrchestrateUseCase._validate_routing("RENAME", {})


def test_validate_routing_still_rejects_agent_without_agent_id():
    with pytest.raises(RequestValidationAppError):
        OrchestrateUseCase._validate_routing("AGENT", {})


def test_validate_routing_still_accepts_absent_type():
    OrchestrateUseCase._validate_routing(None, {})


# ── OrchestrateGraphUseCase._is_rename_request ──────────────────────────────

@pytest.mark.parametrize(
    "parameters, expected",
    [
        (OrchestrationParameters(type="RENAME"), True),
        (OrchestrationParameters(type="AGENT", agent_id="a-1"), False),
        (OrchestrationParameters(type=None), False),
        (OrchestrationParameters(type=""), False),
        (OrchestrationParameters(type="Bogus"), False),
    ],
)
def test_is_rename_request_matrix(parameters, expected):
    assert module.OrchestrateGraphUseCase._is_rename_request(parameters) is expected


# ── OrchestrateGraphUseCase._handle_rename_request ──────────────────────────

@pytest.mark.asyncio
async def test_handle_rename_request_updates_title_and_confirms_owner():
    # Conversation.user_id holds the owner's EMAIL (see OrchestrateUseCase); the command's
    # user_id is a different identity space (a uuid) and must NOT be used for ownership.
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation(user_id="owner@example.com")))
    summary_repo = MagicMock(update_title=MagicMock())
    notifier = MagicMock(send_chat_response=AsyncMock())
    uc = _build_use_case(
        orchestration_state_repo=repo,
        conversation_summary_repo=summary_repo,
        push_gateway_notifier=notifier,
    )
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="  Q3 vendor cleanup  ",
        user_id="uuid-does-not-match-owner-field",
        user_email="owner@example.com",
        parameters=OrchestrationParameters(type="RENAME"),
    )

    await uc._handle_rename_request("conv-1", command)

    summary_repo.update_title.assert_called_once_with("conv-1", "Q3 vendor cleanup")
    notifier.send_chat_response.assert_awaited_once_with(
        conv_id="conv-1", content='Renamed conversation to "Q3 vendor cleanup"'
    )


@pytest.mark.asyncio
async def test_handle_rename_request_strips_control_characters_and_caps_length():
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation(user_id="owner@example.com")))
    summary_repo = MagicMock(update_title=MagicMock())
    uc = _build_use_case(orchestration_state_repo=repo, conversation_summary_repo=summary_repo)
    long_title = "a" * 250
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message=f"\x00\x07{long_title}\n",
        user_id="user-1",
        user_email="owner@example.com",
        parameters=OrchestrationParameters(type="RENAME"),
    )

    await uc._handle_rename_request("conv-1", command)

    saved_title = summary_repo.update_title.call_args.args[1]
    assert "\x00" not in saved_title and "\x07" not in saved_title
    assert len(saved_title) == 200


@pytest.mark.asyncio
async def test_handle_rename_request_denies_non_owner_and_does_not_write():
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation(user_id="owner@example.com")))
    summary_repo = MagicMock(update_title=MagicMock())
    uc = _build_use_case(orchestration_state_repo=repo, conversation_summary_repo=summary_repo)
    uc._emit_error_response = AsyncMock()
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="Sneaky rename",
        user_id="attacker-uuid",
        user_email="attacker@example.com",
        parameters=OrchestrationParameters(type="RENAME"),
    )

    await uc._handle_rename_request("conv-1", command)

    summary_repo.update_title.assert_not_called()
    uc._emit_error_response.assert_awaited_once()
    assert isinstance(uc._emit_error_response.await_args.kwargs["error"], PermissionDeniedError)


@pytest.mark.asyncio
async def test_handle_rename_request_ignores_user_id_uuid_for_ownership():
    """Regression: Conversation.user_id holds the owner's EMAIL, while command.user_id is
    a uuid from a different identity space. A matching user_id must NOT grant ownership —
    only a matching user_email may (otherwise a real owner is always denied in production,
    since their uuid never equals their email)."""
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation(user_id="owner@example.com")))
    summary_repo = MagicMock(update_title=MagicMock())
    uc = _build_use_case(orchestration_state_repo=repo, conversation_summary_repo=summary_repo)
    uc._emit_error_response = AsyncMock()
    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="Sneaky rename",
        user_id="owner@example.com",  # matches conversation.user_id, but is the WRONG field
        user_email="attacker@example.com",
        parameters=OrchestrationParameters(type="RENAME"),
    )

    await uc._handle_rename_request("conv-1", command)

    summary_repo.update_title.assert_not_called()
    uc._emit_error_response.assert_awaited_once()
    assert isinstance(uc._emit_error_response.await_args.kwargs["error"], PermissionDeniedError)


@pytest.mark.asyncio
async def test_handle_rename_request_denies_unknown_conversation_and_does_not_write():
    repo = MagicMock(get_conversation_by=MagicMock(return_value=None))
    summary_repo = MagicMock(update_title=MagicMock())
    uc = _build_use_case(orchestration_state_repo=repo, conversation_summary_repo=summary_repo)
    uc._emit_error_response = AsyncMock()
    command = module.OrchestrationGraphCommand(
        conv_id="conv-missing",
        message="Q3 vendor cleanup",
        user_id="user-1",
        parameters=OrchestrationParameters(type="RENAME"),
    )

    await uc._handle_rename_request("conv-missing", command)

    summary_repo.update_title.assert_not_called()
    uc._emit_error_response.assert_awaited_once()
    assert isinstance(uc._emit_error_response.await_args.kwargs["error"], PermissionDeniedError)


# ── _trigger_graph_for_main_conversation wiring ──────────────────────────────

@pytest.mark.asyncio
async def test_trigger_main_conversation_routes_rename_before_classification():
    guard = MagicMock(
        check=MagicMock(return_value=module.InputGuardResult(passed=True, sanitized_message="ok"))
    )
    repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation(user_id="user-1")))
    uc = _build_use_case(orchestration_state_repo=repo)
    uc.input_guard = guard
    uc._handle_rename_request = AsyncMock()
    uc._classify_and_build_plan = AsyncMock()

    command = module.OrchestrationGraphCommand(
        conv_id="conv-1",
        message="Q3 vendor cleanup",
        user_id="user-1",
        parameters=OrchestrationParameters(type="RENAME"),
    )
    await uc._trigger_graph_for_main_conversation(conv_id="conv-1", command=command)

    uc._handle_rename_request.assert_awaited_once_with("conv-1", command)
    uc._classify_and_build_plan.assert_not_called()


# ── Repository: precedence (highest source wins) ────────────────────────────

class _FakeDb:
    def __init__(self):
        self.writes = []

    def execute_write(self, sql, params=()):
        self.writes.append((sql, params))
        return 1

    def execute_query(self, sql, params=()):
        return []


def test_update_title_writes_user_rename_source():
    from app.layer4_frameworks.repositories.conversation_summary_repository import (
        HanaConversationSummaryRepository,
    )

    db = _FakeDb()
    repo = HanaConversationSummaryRepository(db)

    repo.update_title("conv-1", "Q3 vendor cleanup")

    assert len(db.writes) == 1
    _sql, params = db.writes[0]
    assert params == ("conv-1", "Q3 vendor cleanup", ConversationTitleSource.USER_RENAME.value)
