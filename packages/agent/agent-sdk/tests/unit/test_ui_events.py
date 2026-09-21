def test_ui_event_type_exposes_canonical_chat_events():
    from agent_sdk.layer1_domain.entities.ui_events import UiEventType

    assert UiEventType.CHAT_THINKING.value == "chat:thinking"
    assert UiEventType.CHAT_RESPONSE.value == "chat:response"
    assert UiEventType.CHAT_DISABLED.value == "chat:disabled"
    assert UiEventType.CHAT_ENABLED.value == "chat:enabled"


def test_ui_payload_models_are_typed():
    from agent_sdk.layer1_domain.entities.ui_events import (
        ButtonAction,
        ButtonGroupPayload,
        OpenWorkspacePayload,
        ProgressingCollapsePayload,
        SummaryPayload,
        TextPayload,
        ToolFormPayload,
        UiPayloadType,
        WfInfo,
    )

    collapse = ProgressingCollapsePayload(title="Planning")
    tool_form = ToolFormPayload(
        content="Please complete the workflow form.", wf_info=WfInfo(node_id="node-1")
    )
    text = TextPayload(content="Hello")
    button_group = ButtonGroupPayload(
        buttons=[ButtonAction(label="Approve", value="approve")]
    )
    workspace = OpenWorkspacePayload(
        content="Open the workspace to review generated workflow data."
    )
    summary = SummaryPayload(content="Workflow created successfully.", status="success")

    assert collapse.type == UiPayloadType.PROGRESSING_COLLAPSE.value
    assert tool_form.type == UiPayloadType.TOOL_FORM.value
    assert text.type == UiPayloadType.TEXT.value
    assert button_group.type == UiPayloadType.BUTTON_GROUP.value
    assert workspace.type == UiPayloadType.OPEN_WORKSPACE.value
    assert summary.type == UiPayloadType.SUMMARY.value
    assert collapse.content == "Planning"
    assert tool_form.wf_info.node_id == "node-1"
    assert button_group.text == ["Approve"]


def test_chat_response_event_wraps_typed_payload():
    from agent_sdk.layer1_domain.entities.ui_events import (
        ChatResponseEvent,
        TextPayload,
    )

    event = ChatResponseEvent(
        conversation_id="conv-1",
        payload=TextPayload(text="Done"),
    )

    assert event.type == "chat:response"
    assert event.event_type == "chat:response"
    assert event.conv_id == "conv-1"
    assert event.conversation_id == "conv-1"
    assert event.payload.content == "Done"


def test_chat_toggle_events_default_to_empty_payloads():
    from agent_sdk.layer1_domain.entities.ui_events import (
        ChatDisabledEvent,
        ChatEnabledEvent,
        ChatThinkingEvent,
    )

    thinking = ChatThinkingEvent(conversation_id="conv-1")
    disabled = ChatDisabledEvent(conversation_id="conv-1")
    enabled = ChatEnabledEvent(conversation_id="conv-1")

    assert thinking.type == "chat:thinking"
    assert disabled.type == "chat:disabled"
    assert enabled.type == "chat:enabled"
    assert thinking.payload == {}
    assert disabled.payload == {}
    assert enabled.payload == {}


def test_legacy_payload_aliases_still_populate_canonical_fields():
    from agent_sdk.layer1_domain.entities.ui_events import (
        ButtonAction,
        ButtonGroupPayload,
        ProgressingCollapsePayload,
        TextPayload,
        ToolFormPayload,
    )

    collapse = ProgressingCollapsePayload(message="Validating...")
    text = TextPayload(text="Done")
    button_group = ButtonGroupPayload(
        title="Choose next action",
        buttons=[ButtonAction(label="Yes", value="yes")],
    )
    tool_form = ToolFormPayload(tool_name="search")

    assert collapse.content == "Validating..."
    assert text.content == "Done"
    assert button_group.content == "Choose next action"
    assert button_group.text == ["Yes"]
    assert tool_form.content == "search"
