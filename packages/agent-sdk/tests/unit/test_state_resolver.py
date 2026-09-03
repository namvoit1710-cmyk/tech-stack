from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer1_domain.entities.tenant_context import TenantContext


def test_state_resolver_coerces_sdk_owned_fields_from_mapping():
    from agent_sdk.layer2_application.utils.state_resolver import ensure_state_resolver

    state = ensure_state_resolver(
        {
            "message": "hello",
            "metadata": {"main_conv_id": "main-1", "uploaded_file_ids": ["file-1"]},
            "tenant_context": {
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "conv_id": "conv-1",
                "source": "queue",
                "metadata": {"scope": "agent"},
            },
            "shared_state": {"approved": True},
            "shared_state_key": "workflow-1",
            "shared_state_version": 7,
            "execution_context": {"attempt": 1},
            "context_snapshot": {"step": "review"},
            "uploaded_file_ids": ["file-1"],
        }
    )

    assert state.message == "hello"
    assert state.metadata == ConversationMetadata(
        main_conv_id="main-1",
        sub_conv_ids=[],
        uploaded_file_ids=["file-1"],
    )
    assert state.tenant_context == TenantContext(
        tenant_id="tenant-1",
        user_id="user-1",
        conv_id="conv-1",
        source="queue",
        correlation_id=None,
        metadata={"scope": "agent"},
    )
    assert state.shared_state == {"approved": True}
    assert state.shared_state_key == "workflow-1"
    assert state.shared_state_version == 7
    assert state.execution_context == {"attempt": 1}
    assert state.context_snapshot == {"step": "review"}
    assert state.uploaded_file_ids == ["file-1"]


def test_ensure_state_resolver_returns_existing_resolver_and_missing_fields_are_none():
    from agent_sdk.layer2_application.utils.state_resolver import (
        StateResolver,
        ensure_state_resolver,
    )

    resolver = StateResolver({"message": "hello"})

    assert ensure_state_resolver(resolver) is resolver
    assert resolver.metadata is None
    assert resolver.tenant_context is None
    assert resolver.workflow is None


def test_state_resolver_does_not_mask_workflow_value_present_in_state():
    from agent_sdk.layer2_application.utils.state_resolver import StateResolver

    resolver = StateResolver({"workflow": {"id": "workflow-1"}})

    assert resolver.workflow == {"id": "workflow-1"}


def test_state_resolver_exports_are_available_from_agent_sdk():
    import agent_sdk
    from agent_sdk import StateResolver, ensure_state_resolver

    assert StateResolver is not None
    assert callable(ensure_state_resolver)
    assert "StateResolver" in agent_sdk.__all__
    assert "ensure_state_resolver" in agent_sdk.__all__
