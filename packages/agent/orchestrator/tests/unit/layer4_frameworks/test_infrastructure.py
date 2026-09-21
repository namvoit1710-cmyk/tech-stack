import asyncio
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks import InfrastructureSet
from app.layer4_frameworks.ai.llm_factory import create_llm_provider
from app.layer4_frameworks.clients import create_agent_registry_client, create_workflow_client
from app.layer4_frameworks.security import create_permission_provider


class _Settings(SimpleNamespace):
	INFRA_MODE = "mock"
	LLM_PROVIDER = "mock"
	OPENAI_API_KEY = ""
	OPENAI_MODEL = "gpt-4o-mini"
	WORKFLOW_API_BASE_URL = ""
	PROFILE_API_BASE_URL = ""
	EVENT_MESH_VERIFY_SSL = False
	EVENT_MESH_POLL_INTERVAL_SECONDS = 5
	EVENT_MESH_REQUEST_TIMEOUT_SECONDS = 30


def test_create_llm_provider_returns_mock_by_default():
	provider = create_llm_provider(_Settings())

	assert provider.__class__.__name__ == "MockLLMProvider"


def test_create_llm_provider_falls_back_to_mock_when_api_key_missing():
	settings = _Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="")

	provider = create_llm_provider(settings)

	assert provider.__class__.__name__ == "MockLLMProvider"


def test_create_llm_provider_builds_openai_provider(monkeypatch):
	module = ModuleType("app.layer4_frameworks.ai.openai_llm_provider")

	class FakeOpenAIProvider:
		def __init__(self, api_key: str, model: str):
			self.api_key = api_key
			self.model = model

	module.OpenAILLMProvider = FakeOpenAIProvider
	monkeypatch.setitem(__import__("sys").modules, module.__name__, module)

	provider = create_llm_provider(_Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="token", OPENAI_MODEL="gpt-test"))

	assert isinstance(provider, FakeOpenAIProvider)
	assert provider.api_key == "token"
	assert provider.model == "gpt-test"


def test_create_agent_registry_client_uses_mock_in_mock_mode():
	client = create_agent_registry_client(_Settings())

	assert client.__class__.__name__ == "MockRegistryClient"


def test_create_agent_registry_client_builds_real_client(monkeypatch):
	module = ModuleType("app.layer4_frameworks.clients.agent_registry_client")

	class FakeAgentRegistryClient:
		def __init__(self, base_url: str, logger=None):
			self.base_url = base_url
			self.logger = logger

	module.AgentRegistryClient = FakeAgentRegistryClient
	monkeypatch.setitem(__import__("sys").modules, module.__name__, module)

	client = create_agent_registry_client(_Settings(INFRA_MODE="local", AGENT_REGISTRY_URL="http://registry"))

	assert isinstance(client, FakeAgentRegistryClient)
	assert client.base_url == "http://registry"


def test_create_workflow_client_uses_mock_without_base_url():
	client = create_workflow_client(_Settings())

	assert client.__class__.__name__ == "MockWorkflowClient"


def test_create_workflow_client_builds_real_client(monkeypatch):
	module = ModuleType("app.layer4_frameworks.clients.workflow_client")

	class FakeWorkflowClient:
		def __init__(self, base_url: str):
			self.base_url = base_url

	module.WorkflowClient = FakeWorkflowClient
	monkeypatch.setitem(__import__("sys").modules, module.__name__, module)

	log = object()
	client = create_workflow_client(_Settings(WORKFLOW_API_BASE_URL="http://workflow"))

	assert isinstance(client, FakeWorkflowClient)
	assert client.base_url == "http://workflow"


def test_create_permission_provider_uses_mock_without_profile_api():
	provider = create_permission_provider(_Settings())

	assert provider.__class__.__name__ == "MockPermissionProvider"


def test_create_permission_provider_builds_user_provider(monkeypatch):
	module = ModuleType("app.layer4_frameworks.security.user_permission_provider")

	class FakeUserPermissionProvider:
		def __init__(self, base_url: str):
			self.base_url = base_url

	module.UserPermissionProvider = FakeUserPermissionProvider
	monkeypatch.setitem(__import__("sys").modules, module.__name__, module)

	provider = create_permission_provider(_Settings(PROFILE_API_BASE_URL="http://profile"))

	assert isinstance(provider, FakeUserPermissionProvider)
	assert provider.base_url == "http://profile"


def test_create_infrastructure_composes_dependencies(monkeypatch):
	from app import layer4_frameworks as infrastructure

	monkeypatch.setattr(infrastructure, "create_llm_provider", lambda settings: "llm")
	monkeypatch.setattr(infrastructure, "create_agent_registry_client", lambda settings: "registry")
	monkeypatch.setattr(infrastructure, "create_workflow_client", lambda settings: "workflow")
	monkeypatch.setattr(infrastructure, "create_permission_provider", lambda settings: "permission")

	result = infrastructure.create_infrastructure(_Settings())

	assert result.llm_provider == "llm"
	assert result.agent_registry_client == "registry"
	assert result.workflow_client == "workflow"
	assert result.permission_provider == "permission"

