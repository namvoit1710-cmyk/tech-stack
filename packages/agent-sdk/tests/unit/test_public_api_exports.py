"""Tests that verify the public agent_sdk API surface exposes the ergonomic
helpers introduced in Tasks 01/02 so agent authors can import them without
knowing the internal module structure.

Covers:
- ILLMService protocol importable from agent_sdk
- TenantContext dataclass importable from agent_sdk
- OpenAIService importable from agent_sdk
- llm_factory module/function importable from agent_sdk
- ToolAgentBuilder still importable from agent_sdk
- make_openai_service convenience function importable from agent_sdk
- agent_sdk.__all__ contains each of the above names
- tool_agent_example does NOT import langchain_openai directly and does NOT
  use WorkerToolFactory/HttpWorkerExecutorClient; uses agent_sdk.tool decorator
- The canonical example minimal_tool_agent.py exists and does NOT import
  langchain_openai directly; uses local @tool pattern and no worker imports
"""

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = Path(__file__).parents[2] / "examples"


def _imports_in_source(path: Path) -> list[str]:
    """Return all top-level module names imported in *path* (ast-based)."""
    tree = ast.parse(path.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module.split(".")[0])
    return modules


def _names_imported_from(path: Path, module: str) -> list[str]:
    """Return names imported from *module* in *path* (ast-based)."""
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                names.append(alias.name)
    return names


# ---------------------------------------------------------------------------
# ILLMService
# ---------------------------------------------------------------------------


def test_illmservice_importable_from_agent_sdk():
    """ILLMService protocol should be importable directly from agent_sdk."""
    from agent_sdk import ILLMService  # noqa: F401

    assert isinstance(ILLMService, type) or hasattr(ILLMService, "__protocol_attrs__")


def test_illmservice_in_all():
    import agent_sdk

    assert "ILLMService" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# IChatCompletionService
# ---------------------------------------------------------------------------


def test_ichatcompletionservice_importable_from_agent_sdk():
    from agent_sdk import IChatCompletionService  # noqa: F401

    assert isinstance(IChatCompletionService, type) or hasattr(
        IChatCompletionService, "__protocol_attrs__"
    )


def test_ichatcompletionservice_in_all():
    import agent_sdk

    assert "IChatCompletionService" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# TenantContext
# ---------------------------------------------------------------------------


def test_tenant_context_importable_from_agent_sdk():
    from agent_sdk import TenantContext  # noqa: F401

    assert TenantContext is not None


def test_tenant_context_in_all():
    import agent_sdk

    assert "TenantContext" in agent_sdk.__all__


def test_tenant_context_has_required_fields():
    """TenantContext must expose tenant_id, user_id, and conv_id."""
    import dataclasses

    from agent_sdk import TenantContext

    field_names = {f.name for f in dataclasses.fields(TenantContext)}
    assert "tenant_id" in field_names
    assert "user_id" in field_names
    assert "conv_id" in field_names


# ---------------------------------------------------------------------------
# OpenAIService
# ---------------------------------------------------------------------------


def test_openai_service_importable_from_agent_sdk():
    from agent_sdk import OpenAIService  # noqa: F401

    assert OpenAIService is not None


def test_openai_service_in_all():
    import agent_sdk

    assert "OpenAIService" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# make_openai_service convenience factory
# ---------------------------------------------------------------------------


def test_make_openai_service_importable_from_agent_sdk():
    from agent_sdk import make_openai_service  # noqa: F401

    assert callable(make_openai_service)


def test_make_openai_service_in_all():
    import agent_sdk

    assert "make_openai_service" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# ToolAgentBuilder (still reachable from top-level)
# ---------------------------------------------------------------------------


def test_tool_agent_builder_still_in_agent_sdk():
    from agent_sdk import ToolAgentBuilder  # noqa: F401

    assert ToolAgentBuilder is not None


# ---------------------------------------------------------------------------
# tool_agent_example: must NOT import langchain_openai
# ---------------------------------------------------------------------------


def test_tool_agent_example_does_not_import_langchain_openai():
    """The updated canonical example must not import langchain_openai directly."""
    example_path = _EXAMPLES_DIR / "tool_agent_example.py"
    assert example_path.exists(), f"Example not found: {example_path}"

    imported = _imports_in_source(example_path)
    assert "langchain_openai" not in imported, (
        "tool_agent_example.py still imports langchain_openai directly. "
        "Update the example to use SDK helpers only."
    )


# ---------------------------------------------------------------------------
# minimal_tool_agent.py: must exist and not import langchain_openai
# ---------------------------------------------------------------------------


def test_minimal_tool_agent_example_exists():
    minimal_path = _EXAMPLES_DIR / "minimal_tool_agent.py"
    assert minimal_path.exists(), (
        f"minimal_tool_agent.py not found at {minimal_path}. "
        "Create it as the canonical low-boilerplate example."
    )


def test_minimal_tool_agent_does_not_import_langchain_openai():
    minimal_path = _EXAMPLES_DIR / "minimal_tool_agent.py"
    if not minimal_path.exists():
        pytest.skip("minimal_tool_agent.py does not exist yet")

    imported = _imports_in_source(minimal_path)
    assert "langchain_openai" not in imported, (
        "minimal_tool_agent.py must not import langchain_openai directly. "
        "Use SDK helpers only."
    )


def test_minimal_tool_agent_does_not_import_openai():
    """The minimal example should not directly import openai package either."""
    minimal_path = _EXAMPLES_DIR / "minimal_tool_agent.py"
    if not minimal_path.exists():
        pytest.skip("minimal_tool_agent.py does not exist yet")

    imported = _imports_in_source(minimal_path)
    assert (
        "openai" not in imported
    ), "minimal_tool_agent.py must not import openai directly. Use SDK helpers only."


# ---------------------------------------------------------------------------
# tool decorator
# ---------------------------------------------------------------------------


def test_tool_importable_from_agent_sdk():
    from agent_sdk import tool  # noqa: F401

    assert callable(tool)


def test_tool_in_all():
    import agent_sdk

    assert "tool" in agent_sdk.__all__


def test_tool_registration_importable_from_agent_sdk():
    from agent_sdk import ToolRegistration  # noqa: F401

    assert ToolRegistration is not None


def test_tool_registration_in_all():
    import agent_sdk

    assert "ToolRegistration" in agent_sdk.__all__


def test_itoolregistry_importable_from_agent_sdk():
    from agent_sdk import IToolRegistry  # noqa: F401

    assert IToolRegistry is not None


def test_itoolregistry_in_all():
    import agent_sdk

    assert "IToolRegistry" in agent_sdk.__all__


def test_tool_registrar_importable_from_agent_sdk():
    from agent_sdk import ToolRegistrar  # noqa: F401

    assert ToolRegistrar is not None


def test_tool_registrar_in_all():
    import agent_sdk

    assert "ToolRegistrar" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# tool_agent_example: must use @tool / agent_sdk.tool, no worker imports
# ---------------------------------------------------------------------------


def test_tool_agent_example_does_not_import_worker_tool_factory():
    """tool_agent_example.py must not import WorkerToolFactory."""
    example_path = _EXAMPLES_DIR / "tool_agent_example.py"
    assert example_path.exists(), f"Example not found: {example_path}"

    names = _names_imported_from(example_path, "agent_sdk")
    assert "WorkerToolFactory" not in names, (
        "tool_agent_example.py still imports WorkerToolFactory from agent_sdk. "
        "Replace with local @tool functions."
    )


def test_tool_agent_example_does_not_import_http_worker_executor_client():
    """tool_agent_example.py must not import HttpWorkerExecutorClient."""
    example_path = _EXAMPLES_DIR / "tool_agent_example.py"
    assert example_path.exists(), f"Example not found: {example_path}"

    names = _names_imported_from(example_path, "agent_sdk")
    assert "HttpWorkerExecutorClient" not in names, (
        "tool_agent_example.py still imports HttpWorkerExecutorClient from agent_sdk. "
        "Remove remote worker discovery from the example."
    )


def test_tool_agent_example_imports_tool_from_agent_sdk():
    """tool_agent_example.py must import the 'tool' decorator from agent_sdk."""
    example_path = _EXAMPLES_DIR / "tool_agent_example.py"
    assert example_path.exists(), f"Example not found: {example_path}"

    names = _names_imported_from(example_path, "agent_sdk")
    assert "tool" in names, (
        "tool_agent_example.py does not import 'tool' from agent_sdk. "
        "Use local @tool functions in the example."
    )


# ---------------------------------------------------------------------------
# minimal_tool_agent.py: must use @tool pattern, no worker imports
# ---------------------------------------------------------------------------


def test_minimal_tool_agent_does_not_import_worker_tool_factory():
    """minimal_tool_agent.py must not import WorkerToolFactory."""
    minimal_path = _EXAMPLES_DIR / "minimal_tool_agent.py"
    if not minimal_path.exists():
        pytest.skip("minimal_tool_agent.py does not exist yet")

    names = _names_imported_from(minimal_path, "agent_sdk")
    assert "WorkerToolFactory" not in names, (
        "minimal_tool_agent.py still imports WorkerToolFactory from agent_sdk. "
        "Replace with local @tool functions."
    )


def test_minimal_tool_agent_does_not_import_http_worker_executor_client():
    """minimal_tool_agent.py must not import HttpWorkerExecutorClient."""
    minimal_path = _EXAMPLES_DIR / "minimal_tool_agent.py"
    if not minimal_path.exists():
        pytest.skip("minimal_tool_agent.py does not exist yet")

    names = _names_imported_from(minimal_path, "agent_sdk")
    assert "HttpWorkerExecutorClient" not in names, (
        "minimal_tool_agent.py still imports HttpWorkerExecutorClient from agent_sdk. "
        "Remove remote worker discovery from the example."
    )


def test_minimal_tool_agent_imports_tool_from_agent_sdk():
    """minimal_tool_agent.py must import the 'tool' decorator from agent_sdk."""
    minimal_path = _EXAMPLES_DIR / "minimal_tool_agent.py"
    if not minimal_path.exists():
        pytest.skip("minimal_tool_agent.py does not exist yet")

    names = _names_imported_from(minimal_path, "agent_sdk")
    assert "tool" in names, (
        "minimal_tool_agent.py does not import 'tool' from agent_sdk. "
        "Use local @tool functions in the example."
    )


# ---------------------------------------------------------------------------
# END (langgraph graph sentinel)
# ---------------------------------------------------------------------------


def test_end_importable_from_agent_sdk():
    from agent_sdk import END  # noqa: F401

    assert END is not None


def test_end_in_all():
    import agent_sdk

    assert "END" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# ToolNode (langgraph prebuilt)
# ---------------------------------------------------------------------------


def test_tool_node_importable_from_agent_sdk():
    from agent_sdk import ToolNode  # noqa: F401

    assert ToolNode is not None


def test_tool_node_in_all():
    import agent_sdk

    assert "ToolNode" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# tools_condition (langgraph prebuilt)
# ---------------------------------------------------------------------------


def test_tools_condition_importable_from_agent_sdk():
    from agent_sdk import tools_condition  # noqa: F401

    assert callable(tools_condition)


def test_tools_condition_in_all():
    import agent_sdk

    assert "tools_condition" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# InterruptType (domain entity)
# ---------------------------------------------------------------------------


def test_interrupt_type_importable_from_agent_sdk():
    from agent_sdk import InterruptType  # noqa: F401

    assert InterruptType is not None


def test_interrupt_type_in_all():
    import agent_sdk

    assert "InterruptType" in agent_sdk.__all__


def test_interrupt_type_has_agent_call_value():
    from agent_sdk import InterruptType

    assert InterruptType.AGENT_CALL == "AGENT_CALL"


# ---------------------------------------------------------------------------
# RemoteAgentTool (application service)
# ---------------------------------------------------------------------------


def test_remote_agent_tool_importable_from_agent_sdk():
    from agent_sdk import RemoteAgentTool  # noqa: F401

    assert RemoteAgentTool is not None


def test_remote_agent_tool_in_all():
    import agent_sdk

    assert "RemoteAgentTool" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# IWorkflowEventEmitter
# ---------------------------------------------------------------------------


def test_iworkflow_event_emitter_importable_from_agent_sdk():
    from agent_sdk import IWorkflowEventEmitter  # noqa: F401

    assert IWorkflowEventEmitter is not None


def test_iworkflow_event_emitter_in_all():
    import agent_sdk

    assert "IWorkflowEventEmitter" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# WorkflowEventEmitter
# ---------------------------------------------------------------------------


def test_workflow_event_emitter_importable_from_agent_sdk():
    from agent_sdk import WorkflowEventEmitter  # noqa: F401

    assert WorkflowEventEmitter is not None


def test_workflow_event_emitter_in_all():
    import agent_sdk

    assert "WorkflowEventEmitter" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# serialize_event
# ---------------------------------------------------------------------------


def test_serialize_event_importable_from_agent_sdk():
    from agent_sdk import serialize_event  # noqa: F401

    assert callable(serialize_event)


def test_serialize_event_in_all():
    import agent_sdk

    assert "serialize_event" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# workflow_event_scope
# ---------------------------------------------------------------------------


def test_workflow_event_scope_importable_from_agent_sdk():
    from agent_sdk import workflow_event_scope  # noqa: F401

    assert callable(workflow_event_scope)


def test_workflow_event_scope_in_all():
    import agent_sdk

    assert "workflow_event_scope" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# emit_workflow_event
# ---------------------------------------------------------------------------


def test_emit_workflow_event_importable_from_agent_sdk():
    from agent_sdk import emit_workflow_event  # noqa: F401

    assert callable(emit_workflow_event)


def test_emit_workflow_event_in_all():
    import agent_sdk

    assert "emit_workflow_event" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# AgentDiscoveryService (layer2 application service)
# ---------------------------------------------------------------------------


def test_agent_discovery_service_importable_from_agent_sdk():
    from agent_sdk import AgentDiscoveryService  # noqa: F401

    assert AgentDiscoveryService is not None


def test_agent_discovery_service_in_all():
    import agent_sdk

    assert "AgentDiscoveryService" in agent_sdk.__all__


# ---------------------------------------------------------------------------
# default_tool_factory (layer4 AI helper)
# ---------------------------------------------------------------------------


def test_default_tool_factory_importable_from_agent_sdk():
    from agent_sdk import default_tool_factory  # noqa: F401

    assert callable(default_tool_factory)


def test_default_tool_factory_in_all():
    import agent_sdk

    assert "default_tool_factory" in agent_sdk.__all__


def test_conversation_metadata_importable_from_agent_sdk():
    from agent_sdk import ConversationMetadata  # noqa: F401

    assert ConversationMetadata is not None


def test_event_envelope_importable_from_agent_sdk():
    from agent_sdk import EventEnvelope  # noqa: F401

    assert EventEnvelope is not None


def test_ui_events_importable_from_agent_sdk():
    from agent_sdk import (  # noqa: F401
        ChatResponseEvent,
        SummaryPayload,
        TextPayload,
        UiEventType,
        WfInfo,
    )

    assert UiEventType.CHAT_RESPONSE == "chat:response"
    assert ChatResponseEvent is not None
    assert TextPayload is not None
    assert SummaryPayload is not None
    assert WfInfo is not None


def test_orchestration_contracts_importable_from_agent_sdk():
    from agent_sdk import (  # noqa: F401
        AGENT_PLAN_CREATED,
        OrchestrationEventType,
        StepDefinition,
        TaskDefinition,
    )

    assert AGENT_PLAN_CREATED == "agent.plan.created"
    assert OrchestrationEventType.AGENT_PLAN_CREATED == "agent.plan.created"
    assert TaskDefinition is not None
    assert StepDefinition is not None


def test_new_contracts_in_all():
    import agent_sdk

    expected = {
        "ConversationMetadata",
        "EventEnvelope",
        "UiEventType",
        "WfInfo",
        "TextPayload",
        "SummaryPayload",
        "ChatResponseEvent",
        "OrchestrationEventType",
        "TaskDefinition",
        "StepDefinition",
        "AGENT_PLAN_CREATED",
    }

    assert expected.issubset(set(agent_sdk.__all__))


def test_queue_delivery_and_reaction_exports_are_importable_from_agent_sdk():
    from agent_sdk import (  # noqa: F401
        AsyncAgentDelegator,
        IAgentDelegator,
        IMessageDelivery,
        MessageReactionRouter,
    )

    assert IAgentDelegator is not None
    assert IMessageDelivery is not None
    assert AsyncAgentDelegator is not None
    assert MessageReactionRouter is not None


def test_queue_delivery_and_reaction_exports_are_listed_in_all():
    import agent_sdk

    expected = {
        "IAgentDelegator",
        "IMessageDelivery",
        "AsyncAgentDelegator",
        "MessageReactionRouter",
    }

    assert expected.issubset(set(agent_sdk.__all__))


def test_shared_state_exports_are_importable_from_agent_sdk():
    from agent_sdk import (  # noqa: F401
        HanaSharedStateRepository,
        SharedStateDocument,
        SharedStateLockInfo,
        SharedStateRecord,
        extract_state_snapshot,
        merge_state_snapshot,
    )

    assert SharedStateDocument is not None
    assert SharedStateLockInfo is not None
    assert SharedStateRecord is not None
    assert callable(extract_state_snapshot)
    assert callable(merge_state_snapshot)
    assert HanaSharedStateRepository is not None


def test_registration_contract_exports_are_importable_from_agent_sdk():
    from agent_sdk import (  # noqa: F401
        AgentRuntimeConfig,
        ExecutionPolicy,
        QueueMetadata,
        RegistryEndpointResolver,
    )

    assert AgentRuntimeConfig is not None
    assert ExecutionPolicy is not None
    assert QueueMetadata is not None
    assert RegistryEndpointResolver is not None


def test_error_contract_exports_are_importable_from_agent_sdk():
    from agent_sdk import (  # noqa: F401
        BusinessRuleException,
        DelegationTimeoutException,
        QueueDeliveryException,
        WorkflowKnownIssueException,
    )

    assert BusinessRuleException is not None
    assert WorkflowKnownIssueException is not None
    assert DelegationTimeoutException is not None
    assert QueueDeliveryException is not None


def test_resolver_exports_are_importable_from_agent_sdk():
    from agent_sdk import (  # noqa: F401
        StateResolver,
        ensure_state_resolver,
    )

    assert StateResolver is not None
    assert callable(ensure_state_resolver)


def test_queue_first_exports_are_listed_in_all():
    import agent_sdk

    expected = {
        "AgentRuntimeConfig",
        "ExecutionPolicy",
        "QueueMetadata",
        "RegistryEndpointResolver",
        "SharedStateDocument",
        "SharedStateLockInfo",
        "SharedStateRecord",
        "HanaSharedStateRepository",
        "BusinessRuleException",
        "WorkflowKnownIssueException",
        "DelegationTimeoutException",
        "QueueDeliveryException",
        "StateResolver",
        "ensure_state_resolver",
        "extract_state_snapshot",
        "merge_state_snapshot",
    }

    assert expected.issubset(set(agent_sdk.__all__))
