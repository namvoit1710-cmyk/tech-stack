"""Gap-fill unit specs for low-coverage layer2 conversation/dispatch use cases,
the graph main_node, and layer1 pure rules (permission collector, agent selector).

Grounded in source:
  - ListConversationsUseCase        — page clamping, saved-title precedence,
                                       context fallback, payload-content fallback,
                                       "Untitled conversation" default
  - GetConversationUseCase          — not-found -> GetConversationError; None repo -> RuntimeError
  - GetPermissionsUseCase           — maps PermissionContext; None provider -> RuntimeError
  - GetDispatchResultUseCase        — no-state error, no-task error, current-task selection
  - main_node                       — resolves "orchestrate_graph" use case and awaits it
  - PermissionCollector             — tier default quotas, explicit quotas, unknown tier
  - AgentSelector                   — published+alive precedence, fallback, empty list
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    Conversation,
    GlobalContext,
    Metadata,
    Task,
    TaskStatus,
)
from app.layer1_domain.entities.agent.scored_agent import ScoredAgent
from app.layer1_domain.entities.permission.permission_collector import PermissionCollector


# ══════════════════════════════ layer1: PermissionCollector ═════════════════

class TestPermissionCollector:
    def test_uses_explicit_resource_quotas_when_present(self):
        ctx = PermissionCollector.collect(
            "u1",
            {
                "roles": ["admin"],
                "allowed_agents": ["a1"],
                "subscription_tier": "premium",
                "resource_quotas": {"max_requests_per_hour": 5000},
            },
        )
        assert ctx.user_id == "u1"
        assert ctx.roles == ("admin",)
        assert ctx.allowed_agents == ("a1",)
        assert ctx.subscription_tier == "premium"
        assert ctx.resource_quotas == {"max_requests_per_hour": 5000}

    def test_falls_back_to_tier_default_quota_for_standard(self):
        ctx = PermissionCollector.collect("u1", {"subscription_tier": "standard"})
        assert ctx.resource_quotas == {"max_requests_per_hour": 100}

    def test_unknown_tier_defaults_to_free_quota(self):
        # BOUNDARY: an unrecognized tier maps to the "free" default (source :32).
        ctx = PermissionCollector.collect("u1", {"subscription_tier": "enterprise-xl"})
        assert ctx.resource_quotas == {"max_requests_per_hour": 10}

    def test_missing_tier_defaults_to_free(self):
        ctx = PermissionCollector.collect("u1", {})
        assert ctx.subscription_tier == "free"
        assert ctx.resource_quotas == {"max_requests_per_hour": 10}


# ══════════════════════════════ layer1: AgentSelector ═══════════════════════

def _agent(agent_id, *, published, alive):
    return ScoredAgent(
        id=agent_id, name=agent_id, kind="business", status="ACTIVE",
        is_published=published, description="", healthcheck_endpoint="",
        invoke_endpoint="", is_alive=alive, last_heartbeat=None, version="1.0",
        config_type="runtime", capabilities=[], agents=[], workflows=[], tools=[],
        metadata={}, business="mdg", created_at=None, updated_at=None,
    )


class TestAgentSelector:
    @pytest.mark.asyncio
    async def test_prefers_first_published_and_alive_agent(self):
        from app.layer1_domain.entities.classifier.agent_selector import AgentSelector

        agents = [_agent("dead", published=True, alive=False), _agent("live", published=True, alive=True)]
        assert (await AgentSelector().select_agent(agents)).id == "live"

    @pytest.mark.asyncio
    async def test_falls_back_to_first_agent_when_none_eligible(self):
        from app.layer1_domain.entities.classifier.agent_selector import AgentSelector

        agents = [_agent("only", published=False, alive=False)]
        assert (await AgentSelector().select_agent(agents)).id == "only"

    @pytest.mark.asyncio
    async def test_empty_list_returns_none(self):
        from app.layer1_domain.entities.classifier.agent_selector import AgentSelector

        assert await AgentSelector().select_agent([]) is None


# ══════════════════════════ layer2: ListConversationsUseCase ════════════════

def _list_uc(*, agent_event_repo=None, summary_repo=None, state_repo=None):
    from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import (
        ListConversationsUseCase,
    )

    return ListConversationsUseCase(
        logger=MagicMock(),
        monitor=MagicMock(),
        agent_event_repo=agent_event_repo or MagicMock(),
        conversation_summary_repo=summary_repo,
        orchestration_state_repo=state_repo,
    )


def _event(conv_id="c1", *, payload=None):
    ev = MagicMock()
    ev.conv_id = conv_id
    ev.timestamp = MagicMock(isoformat=MagicMock(return_value="2026-07-02T00:00:00"))
    ev.payload = payload
    return ev


class TestListConversationsUseCase:
    def test_clamps_page_index_and_size_to_minimum_one(self):
        from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import (
            ListConversationsInput,
        )

        repo = MagicMock(list_latest_conversation_events=MagicMock(return_value=[]))
        uc = _list_uc(agent_event_repo=repo)

        out = uc.execute(ListConversationsInput(user_id="u1", page_index=-3, page_size=0))

        assert out.page_index == 1 and out.page_size == 1
        # offset = (1-1)*1 = 0
        repo.list_latest_conversation_events.assert_called_once_with(skip=0, top=1)

    def test_saved_title_takes_precedence(self):
        from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import (
            ListConversationsInput,
        )

        repo = MagicMock(list_latest_conversation_events=MagicMock(return_value=[_event("c1")]))
        saved = MagicMock()
        saved.title = "Saved Title"
        summary = MagicMock(get_titles=MagicMock(return_value={"c1": saved}))
        uc = _list_uc(agent_event_repo=repo, summary_repo=summary)

        out = uc.execute(ListConversationsInput(user_id="u1"))

        assert out.conversations[0].title == "Saved Title"

    def test_falls_back_to_payload_content_when_no_saved_title_or_state(self):
        from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import (
            ListConversationsInput,
        )

        repo = MagicMock(
            list_latest_conversation_events=MagicMock(
                return_value=[_event("c1", payload={"content": "First message"})]
            )
        )
        uc = _list_uc(agent_event_repo=repo)  # no summary repo, no state repo

        out = uc.execute(ListConversationsInput(user_id="u1"))

        assert out.conversations[0].title == "First message"

    def test_defaults_to_untitled_when_no_source_available(self):
        from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import (
            ListConversationsInput,
        )

        repo = MagicMock(
            list_latest_conversation_events=MagicMock(return_value=[_event("c1", payload={})])
        )
        uc = _list_uc(agent_event_repo=repo)

        out = uc.execute(ListConversationsInput(user_id="u1"))

        assert out.conversations[0].title == "Untitled conversation"


# ══════════════════════════ layer2: GetConversationUseCase ══════════════════

class TestGetConversationUseCase:
    def test_returns_output_with_events(self):
        from app.layer2_application.features.conversation.use_cases.get_conversation_use_case import (
            GetConversationInput,
            GetConversationOutput,
            GetConversationUseCase,
        )

        repo = MagicMock(get_events_by_conversation_id=MagicMock(return_value=["e1", "e2"]))
        uc = GetConversationUseCase(logger=MagicMock(), monitor=MagicMock(), agent_event_repo=repo)

        out = uc.execute(GetConversationInput(conversation_id="c1"))

        assert isinstance(out, GetConversationOutput)
        assert out.events == ["e1", "e2"]

    def test_no_events_returns_conversation_error(self):
        from app.layer2_application.features.conversation.use_cases.get_conversation_use_case import (
            GetConversationError,
            GetConversationInput,
            GetConversationUseCase,
        )

        repo = MagicMock(get_events_by_conversation_id=MagicMock(return_value=[]))
        uc = GetConversationUseCase(logger=MagicMock(), monitor=MagicMock(), agent_event_repo=repo)

        out = uc.execute(GetConversationInput(conversation_id="missing"))

        assert isinstance(out, GetConversationError)
        assert out.error == "Conversation not found"
        assert out.conversation_id == "missing"

    def test_none_repo_raises_runtime_error(self):
        from app.layer2_application.features.conversation.use_cases.get_conversation_use_case import (
            GetConversationInput,
            GetConversationUseCase,
        )

        uc = GetConversationUseCase(logger=MagicMock(), monitor=MagicMock(), agent_event_repo=None)

        with pytest.raises(RuntimeError):
            uc.execute(GetConversationInput(conversation_id="c1"))


# ══════════════════════════ layer2: GetPermissionsUseCase ═══════════════════

class TestGetPermissionsUseCase:
    def test_maps_permission_context_to_output(self):
        from app.layer2_application.features.conversation.use_cases.get_permissions_use_case import (
            GetPermissionsInput,
            GetPermissionsUseCase,
        )

        provider = MagicMock(
            get_user_permissions=MagicMock(
                return_value={
                    "roles": ["user", "admin"],
                    "allowed_agents": ["a1"],
                    "subscription_tier": "premium",
                    "resource_quotas": {"max_requests_per_hour": 1000},
                }
            )
        )
        uc = GetPermissionsUseCase(logger=MagicMock(), monitor=MagicMock(), permission_provider=provider)

        out = uc.execute(GetPermissionsInput(user_id="u1"))

        assert out.user_id == "u1"
        assert out.roles == ["user", "admin"]
        assert out.allowed_agents == ["a1"]
        assert out.subscription_tier == "premium"
        assert out.resource_quotas == {"max_requests_per_hour": 1000}

    def test_none_provider_raises_runtime_error(self):
        from app.layer2_application.features.conversation.use_cases.get_permissions_use_case import (
            GetPermissionsInput,
            GetPermissionsUseCase,
        )

        uc = GetPermissionsUseCase(logger=MagicMock(), monitor=MagicMock(), permission_provider=None)

        with pytest.raises(RuntimeError):
            uc.execute(GetPermissionsInput(user_id="u1"))


# ══════════════════════════ layer2: GetDispatchResultUseCase ════════════════

def _conversation_with_tasks(*tasks, conv_id="c1"):
    conv = Conversation(
        conv_id=conv_id,
        user_id="u1",
        shared_state=AgentSharedState(
            metadata=Metadata(conv_id=conv_id, checkpoint_id="CP-01"),
            context=GlobalContext(),
            executions=list(tasks),
        ),
    )
    return conv


def _task(task_id, status):
    return Task(
        task_id=task_id, agent="agent.a1", name="t", next_task_id=None,
        workflow_id="", agent_id="a1", status=status, parent_task_id="",
        depend_on=[], input_data={}, output_data={}, error_detail=None,
    )


class TestGetDispatchResultUseCase:
    def test_no_state_returns_error(self):
        from app.layer2_application.features.orchestrate.use_cases.get_dispatch_result_use_case import (
            GetDispatchResultInput,
            GetDispatchResultUseCase,
        )

        repo = MagicMock(get_conversation_by=MagicMock(return_value=None))
        uc = GetDispatchResultUseCase(logger=MagicMock(), monitor=MagicMock(), orchestration_state_repo=repo)

        out = uc.execute(GetDispatchResultInput(conv_id="c1"))

        assert out.error == "Shared state not found"
        assert out.current_task is None

    def test_no_tasks_returns_error_with_shared_state(self):
        from app.layer2_application.features.orchestrate.use_cases.get_dispatch_result_use_case import (
            GetDispatchResultInput,
            GetDispatchResultUseCase,
        )

        repo = MagicMock(get_conversation_by=MagicMock(return_value=_conversation_with_tasks()))
        uc = GetDispatchResultUseCase(logger=MagicMock(), monitor=MagicMock(), orchestration_state_repo=repo)

        out = uc.execute(GetDispatchResultInput(conv_id="c1"))

        assert out.error == "No tasks found in shared state"
        assert out.shared_state is not None

    def test_selects_first_pending_or_processing_task(self):
        from app.layer2_application.features.orchestrate.use_cases.get_dispatch_result_use_case import (
            GetDispatchResultInput,
            GetDispatchResultUseCase,
        )

        conv = _conversation_with_tasks(
            _task("t1", TaskStatus.SUCCESS), _task("t2", TaskStatus.PENDING)
        )
        repo = MagicMock(get_conversation_by=MagicMock(return_value=conv))
        uc = GetDispatchResultUseCase(logger=MagicMock(), monitor=MagicMock(), orchestration_state_repo=repo)

        out = uc.execute(GetDispatchResultInput(conv_id="c1"))

        assert out.current_task.id == "t2"
        assert out.error is None

    def test_falls_back_to_last_task_when_none_active(self):
        # BOUNDARY: no PENDING/PROCESSING task -> _select_current_task returns the
        # last execution (source :66).
        from app.layer2_application.features.orchestrate.use_cases.get_dispatch_result_use_case import (
            GetDispatchResultInput,
            GetDispatchResultUseCase,
        )

        conv = _conversation_with_tasks(
            _task("t1", TaskStatus.SUCCESS), _task("t2", TaskStatus.SUCCESS)
        )
        repo = MagicMock(get_conversation_by=MagicMock(return_value=conv))
        uc = GetDispatchResultUseCase(logger=MagicMock(), monitor=MagicMock(), orchestration_state_repo=repo)

        out = uc.execute(GetDispatchResultInput(conv_id="c1"))

        assert out.current_task.id == "t2"


# ══════════════════════════ layer2: graph main_node ═════════════════════════

class TestMainNode:
    @pytest.mark.asyncio
    async def test_main_node_resolves_orchestrate_graph_usecase_and_executes(self, monkeypatch):
        from app.layer2_application.graphs.nodes import main_node as main_node_module

        resolved = MagicMock(execute=AsyncMock(return_value={"result": "ok"}))
        monkeypatch.setattr(main_node_module, "get_usecase", MagicMock(return_value=resolved))

        state = {"parameters": {"event": {"conv_id": "c1", "message": "hi"}}}
        out = await main_node_module.main_node(state)

        main_node_module.get_usecase.assert_called_once_with("orchestrate_graph")
        resolved.execute.assert_awaited_once()
        # command is built from event_data and carries the conv_id
        command = resolved.execute.await_args.args[0]
        assert command.conv_id == "c1"
        assert out == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_main_node_defaults_to_empty_event(self, monkeypatch):
        # BOUNDARY: missing parameters/event -> command built from {} (empty defaults).
        from app.layer2_application.graphs.nodes import main_node as main_node_module

        resolved = MagicMock(execute=AsyncMock(return_value={}))
        monkeypatch.setattr(main_node_module, "get_usecase", MagicMock(return_value=resolved))

        await main_node_module.main_node({})

        command = resolved.execute.await_args.args[0]
        assert command.conv_id == ""
