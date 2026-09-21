import pytest

from app.layer4_frameworks.ai.mock_llm_provider import MockLLMProvider
from app.layer4_frameworks.clients.mock_agent_client import MockAgentClient
from app.layer4_frameworks.clients.mock_registry_client import MockRegistryClient
from app.layer4_frameworks.clients.mock_workflow_client import MockWorkflowClient
from app.layer4_frameworks.security.mock_permission_provider import MockPermissionProvider
from app.layer1_domain.entities.permission.permission_context import PermissionContext


class TestMockRegistryClient:
    @pytest.mark.asyncio
    async def test_filter_agents_returns_published_business_agents(self):
        client = MockRegistryClient()
        agents = await client.filter_agents(kind="BUSINESS", is_published=True)

        assert [agent.id for agent in agents] == ["agent-inventory", "agent-data-validation", "agent-data-import"]
        assert all(agent.kind == "BUSINESS" for agent in agents)
        assert all(agent.is_published is True for agent in agents)

    @pytest.mark.asyncio
    async def test_filter_agents_can_filter_by_status(self):
        client = MockRegistryClient()
        agents = await client.filter_agents(status="ACTIVE")

        assert {agent.id for agent in agents} == {
            "agent-inventory",
            "agent-data-validation",
            "agent-file-mgmt",
        }
        assert all(agent.status == "ACTIVE" for agent in agents)


class TestMockLLMProvider:
    def test_classify_with_agents(self):
        provider = MockLLMProvider()
        agents = [
            {
                "name": "Math Agent",
                "supported_domains": ["math"],
                "capabilities": ["math.calculate"],
                "supported_intents": ["calculate", "compute"],
            }
        ]
        result = provider.classify_intent("calculate 5 + 3", {"agents": agents})
        assert result["domain"] == "math"
        assert result["confidence"] > 0.5
        assert result["parameters"].get("expression") == "5 + 3"

    def test_classify_fallback(self):
        provider = MockLLMProvider()
        result = provider.classify_intent("random unknown text", {"agents": []})
        assert result["domain"] == "general"
        assert result["target_action"] == "chat"

    def test_classify_with_agents_returns_target_action(self):
        provider = MockLLMProvider()
        agents = [
            {
                "name": "Data Import Agent",
                "supported_domains": ["data_import"],
                "capabilities": ["data_import.import_csv"],
                "supported_intents": ["import", "upload"],
            }
        ]
        result = provider.classify_intent("import my file", {"agents": agents})
        assert result["target_action"] == "import_csv"


class TestMockAgentClient:
    def test_list_agents_returns_expected_catalog(self):
        client = MockAgentClient()

        agents = client.list_agents()

        assert len(agents) == 4
        assert agents[0]["id"] == "data-import-agent"
        assert "validate" in agents[1]["capabilities"]

    def test_dispatch_to_agent_returns_mock_completion_message(self):
        client = MockAgentClient()

        result = client.dispatch_to_agent(
            "data-import-agent",
            {"message": "Import this file"},
            PermissionContext(user_id="user-1"),
        )

        assert result == {
            "message": "[Mock] Agent 'data-import-agent' processed: Import this file",
            "agent_id": "data-import-agent",
            "status": "completed",
        }


class TestMockWorkflowClient:
    @pytest.mark.asyncio
    async def test_async_lookup_methods_return_none(self):
        client = MockWorkflowClient()

        assert await client.get_workflow_by_path("path") is None
        assert await client.get_workflow_by_id("wf-1") is None
        assert await client.get_run("run-1") is None

    def test_sync_lookup_methods_return_none(self):
        client = MockWorkflowClient()

        assert client.get_run_sync("run-1") is None
        assert client.get_workflow_definition("wf-1") is None
        assert client.replay_pending_requests("run-1") is None


class TestMockPermissionProvider:
    def test_returns_default_permissions(self):
        provider = MockPermissionProvider()

        permissions = provider.get_user_permissions("user-1")

        assert permissions["roles"] == ["user", "viewer"]
        assert permissions["resource_quotas"]["max_requests_per_hour"] == 100

    def test_returns_default_feature_flags(self):
        provider = MockPermissionProvider()

        features = provider.get_user_features("user@example.com", "Bearer token")

        assert features["email"] == "user@example.com"
        assert features["feature_flags"]["beta_tools"] is False