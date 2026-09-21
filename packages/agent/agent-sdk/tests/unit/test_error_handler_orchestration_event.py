from unittest.mock import AsyncMock, MagicMock

import pytest


def test_error_handler_node_uses_serialized_error_context_semantics():
    from agent_sdk.layer2_application.services.middleware.error_handler_node import (
        error_handler_node,
    )

    state = {
        "error": "Fallback text that should be replaced",
        "error_context": {
            "exception_type": "BusinessRuleException",
            "message": "Planner rejected duplicate request",
            "error_code": "BUSINESS_RULE_DUPLICATE",
            "related_step_id": "planner-1",
            "is_critical": False,
            "metadata": {"reason": "duplicate"},
        },
        "conv_id": "main_123",
    }

    result = error_handler_node(state, {"logger": MagicMock()})

    assert result["transport_state"] == "ERROR"
    assert result["formatted_response"] == {
        "type": "error",
        "status": "error",
        "content": "Planner rejected duplicate request",
        "error": "Planner rejected duplicate request",
        "error_code": "BUSINESS_RULE_DUPLICATE",
        "conv_id": "main_123",
        "related_step_id": "planner-1",
        "is_critical": False,
        "error_context": {
            "exception_type": "BusinessRuleException",
            "message": "Planner rejected duplicate request",
            "error_code": "BUSINESS_RULE_DUPLICATE",
            "related_step_id": "planner-1",
            "is_critical": False,
            "metadata": {"reason": "duplicate"},
        },
    }


@pytest.mark.asyncio
async def test_emit_orchestration_event_serializes_semantic_error_payload_to_reply_scope():
    from agent_sdk.layer1_domain.entities.conversation_metadata import (
        ConversationMetadata,
    )
    from agent_sdk.layer1_domain.entities.orchestration_events import (
        create_agent_plan_error_event,
    )
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=publisher)
    request = ExecuteAgentInput(
        message="hello",
        conv_id="main_orch_2",
        correlation_id="corr-orch-2",
        reply_to="planner.reply",
        metadata=ConversationMetadata(main_conv_id="main_orch_2"),
    )

    with workflow_event_scope(emitter, request=request, mode="execute"):
        await emitter.emit_orchestration_event(
            create_agent_plan_error_event(
                "main_orch_2",
                {
                    "message": "Agent step failed",
                    "error_code": "DELEGATION_TIMEOUT",
                    "related_step_id": "delegate-1",
                    "is_critical": True,
                    "error_context": {
                        "exception_type": "DelegationTimeoutException",
                        "message": "Agent step failed",
                        "error_code": "DELEGATION_TIMEOUT",
                        "related_step_id": "delegate-1",
                        "is_critical": True,
                        "metadata": {"agent_type": "planner"},
                    },
                },
            )
        )

    topic, payload = publisher.publish.call_args.args[:2]
    assert topic == "planner.reply.progress"
    assert payload["type"] == "agent.plan.error"
    assert payload["conversation_id"] == "main_orch_2"
    assert payload["correlation_id"] == "corr-orch-2"
    assert payload["payload"] == {
        "message": "Agent step failed",
        "error_code": "DELEGATION_TIMEOUT",
        "related_step_id": "delegate-1",
        "is_critical": True,
        "error_context": {
            "exception_type": "DelegationTimeoutException",
            "message": "Agent step failed",
            "error_code": "DELEGATION_TIMEOUT",
            "related_step_id": "delegate-1",
            "is_critical": True,
            "metadata": {"agent_type": "planner"},
        },
    }
