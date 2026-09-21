import pytest


def test_semantic_exceptions_capture_structured_fields():
    from agent_sdk.layer1_domain.exceptions import BusinessRuleException

    exc = BusinessRuleException(
        "Policy prevents this action",
        error_code="BUSINESS_RULE_BLOCKED",
        related_step_id="step-7",
        is_critical=False,
        metadata={"policy": "budget"},
    )

    assert str(exc) == "Policy prevents this action"
    assert exc.message == "Policy prevents this action"
    assert exc.error_code == "BUSINESS_RULE_BLOCKED"
    assert exc.related_step_id == "step-7"
    assert exc.is_critical is False
    assert exc.metadata == {"policy": "budget"}
    assert exc.to_error_context() == {
        "exception_type": "BusinessRuleException",
        "message": "Policy prevents this action",
        "error_code": "BUSINESS_RULE_BLOCKED",
        "related_step_id": "step-7",
        "is_critical": False,
        "metadata": {"policy": "budget"},
    }


def test_node_execution_error_preserves_structured_context_from_semantic_exception():
    from agent_sdk.layer1_domain.exceptions import (
        NodeExecutionError,
        WorkflowKnownIssueException,
    )

    cause = WorkflowKnownIssueException(
        "Known downstream outage",
        error_code="KNOWN_ISSUE_DOWNSTREAM",
        related_step_id="fetch-data",
        is_critical=True,
        metadata={"system": "sap"},
    )
    exc = NodeExecutionError(str(cause), error_context=cause.to_error_context())

    assert exc.error_context == {
        "exception_type": "WorkflowKnownIssueException",
        "message": "Known downstream outage",
        "error_code": "KNOWN_ISSUE_DOWNSTREAM",
        "related_step_id": "fetch-data",
        "is_critical": True,
        "metadata": {"system": "sap"},
    }


@pytest.mark.parametrize(
    ("exception_name", "error_code"),
    [
        ("BusinessRuleException", "BUSINESS_RULE_ERROR"),
        ("WorkflowKnownIssueException", "WORKFLOW_KNOWN_ISSUE"),
        ("DelegationTimeoutException", "DELEGATION_TIMEOUT"),
        ("QueueDeliveryException", "QUEUE_DELIVERY_ERROR"),
    ],
)
def test_semantic_exception_types_are_importable(exception_name: str, error_code: str):
    from agent_sdk.layer1_domain import exceptions as exc_module

    exc_type = getattr(exc_module, exception_name)
    exc = exc_type("boom", error_code=error_code)

    assert exc.error_code == error_code
