from dataclasses import fields


def test_conversation_metadata_exposes_canonical_fields():
    from agent_sdk.layer1_domain.entities.conversation_metadata import (
        ConversationMetadata,
    )

    field_names = {field.name for field in fields(ConversationMetadata)}

    assert field_names == {"main_conv_id", "sub_conv_ids", "uploaded_file_ids"}


def test_conversation_metadata_defaults_are_independent():
    from agent_sdk.layer1_domain.entities.conversation_metadata import (
        ConversationMetadata,
    )

    first = ConversationMetadata()
    second = ConversationMetadata()

    first.sub_conv_ids.append("sub-1")
    first.uploaded_file_ids.append("file-1")

    assert first.main_conv_id == ""
    assert second.sub_conv_ids == []
    assert second.uploaded_file_ids == []


def test_agent_request_includes_queue_and_metadata_fields():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
    from agent_sdk.layer1_domain.entities.conversation_metadata import (
        ConversationMetadata,
    )

    request = AgentRequest(message="hello")

    assert request.session_id == ""
    assert request.metadata == ConversationMetadata()
    assert request.uploaded_file_ids == []
    assert request.execution_context == {}
    assert request.context_snapshot == {}


def test_agent_base_state_annotations_include_queue_and_metadata_fields():
    from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

    expected_fields = {
        "session_id",
        "metadata",
        "uploaded_file_ids",
        "execution_context",
        "context_snapshot",
    }

    assert expected_fields.issubset(AgentBaseState.__annotations__)
