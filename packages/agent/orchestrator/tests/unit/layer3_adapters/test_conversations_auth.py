import httpx
import pytest
from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
from app.layer1_domain.entities.orchestrator.ui_event import UIEventRole

import importlib.util, os
# Load the local orchestrator main.py explicitly to avoid agent-sdk's main
spec = importlib.util.spec_from_file_location("main", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "main.py")))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
create_app = main.create_app
from app.layer2_application.features.conversation.use_cases.get_conversation_use_case import GetConversationOutput
from app.layer2_application.features.conversation.use_cases.get_conversation_messages_use_case import GetConversationMessagesOutput, ReopenConversationEvent
from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import ConversationSummaryItem, ListConversationsOutput


class StubListConversationsUseCase:
    def __init__(self):
        self.last_request = None

    def execute(self, request):
        self.last_request = request
        return ListConversationsOutput(
            conversations=[
                ConversationSummaryItem(
                    conversation_id="conv-001",
                    title="Create change request from upload",
                    timestamp="2024-01-01T00:00:00+00:00",
                )
            ],
            page_index=request.page_index,
            page_size=request.page_size,
        )


class StubGetConversationUseCase:
    def __init__(self):
        self.last_request = None

    def execute(self, request):
        self.last_request = request
        return GetConversationOutput(
            conversation_id=request.conversation_id,
            events=[
                AgentEvent(
                    event_id="evt-1",
                    event_type="main:user:chat",
                    conv_id=request.conversation_id,
                    payload={"id": "payload-1", "content": "Open conversation"},
                )
            ],
        )


class StubGetConversationMessagesUseCase:
    def __init__(self):
        self.last_request = None

    def execute(self, request):
        self.last_request = request
        return GetConversationMessagesOutput(
            messages=[
                ReopenConversationEvent(
                    id="uuid-disable",
                    role=UIEventRole.ASSISTANT,
                    event_type="chat:disable",
                    content="",
                    created_at="",
                    timestamp="",
                ),
                ReopenConversationEvent(
                    id="uuid-001",
                    role=UIEventRole.USER,
                    content="Open conversation",
                    created_at="2024-01-01T00:00:00+00:00",
                    timestamp="2024-01-01T00:00:00+00:00",
                ),
                ReopenConversationEvent(
                    id="uuid-enable",
                    role=UIEventRole.ASSISTANT,
                    event_type="chat:enable",
                    content="",
                    created_at="",
                    timestamp="",
                ),
                ReopenConversationEvent(
                    id="uuid-002",
                    role=UIEventRole.USER,
                    content="Follow-up question",
                    created_at="2024-01-01T00:01:00+00:00",
                    timestamp="2024-01-01T00:01:00+00:00",
                ),
            ],
            total=4,
        )



def _make_client():
    main.build_app_container = lambda: {}
    app = create_app()
    list_usecase = StubListConversationsUseCase()
    get_usecase = StubGetConversationUseCase()
    get_messages_usecase = StubGetConversationMessagesUseCase()
    app.state.container = {
        "_usecases": {
            "conversation.list_conversations": list_usecase,
            "conversation.get_conversation": get_usecase,
            "conversation.get_conversation_messages": get_messages_usecase,
        },
    }
    app.state.test_list_usecase = list_usecase
    app.state.test_get_usecase = get_usecase
    app.state.test_get_messages_usecase = get_messages_usecase
    return app


@pytest.mark.asyncio
async def test_list_conversations_uses_query_mapping():
    app = _make_client()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/conversations?user_id=u123&tenant_id=t1&page_index=2&page_size=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["page_index"] == 2
    assert payload["page_size"] == 5
    assert payload["conversations"][0]["conversation_id"] == "conv-001"
    assert payload["conversations"][0]["title"] == "Create change request from upload"
    assert payload["conversations"][0]["timestamp"] == "2024-01-01T00:00:00+00:00"
    request = app.state.test_list_usecase.last_request
    assert request.user_id == "u123"
    assert request.tenant_id == "t1"
    assert request.page_index == 2
    assert request.page_size == 5


@pytest.mark.asyncio
async def test_get_conversation_strips_whitespace_from_path_id():
    app = _make_client()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/conversations/%20conv-001%20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == "conv-001"
    assert payload["events"][0]["conv_id"] == "conv-001"
    request = app.state.test_get_usecase.last_request
    assert request.conversation_id == "conv-001"


@pytest.mark.asyncio
async def test_get_conversation_messages_strips_whitespace_from_path_id():
    app = _make_client()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/conversations/%20conv-001%20/messages")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["messages"][0]["event_type"] == "chat:disable"
    assert payload["messages"][1]["role"] == "user"
    assert payload["messages"][1]["created_at"] == "2024-01-01T00:00:00+00:00"
    assert payload["messages"][1]["timestamp"] == "2024-01-01T00:00:00+00:00"
    assert payload["messages"][2]["event_type"] == "chat:enable"
    assert payload["messages"][3]["content"] == "Follow-up question"
    request = app.state.test_get_messages_usecase.last_request
    assert request.conversation_id == "conv-001"

