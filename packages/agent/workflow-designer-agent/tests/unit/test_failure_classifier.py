from __future__ import annotations

from app.layer1_domain.entities.failure_classifier import FailureClassifier, RepairClass


def test_workflow_validation_error_is_always_class_a() -> None:
    assert FailureClassifier.classify(
        error_code="", message="Duplicate node ids: ['a']", is_workflow_validation_error=True,
    ) is RepairClass.A


def test_class_a_error_codes_are_auto_fixable() -> None:
    for code in (
        "VALIDATION_ERROR", "INVALID_WORKFLOW", "INVALID_NODE_DATA", "INVALID_EDGE_DATA",
        "INVALID_EDGE_REFERENCE", "NO_HANDLER", "NOTHING_TO_EDIT",
    ):
        assert FailureClassifier.classify(error_code=code) is RepairClass.A, code


def test_class_b_error_codes_need_the_user() -> None:
    for code in ("SERVICE_NOT_CONFIGURED", "GATEWAY_SPEC_FETCH_FAILED", "GATEWAY_SPEC_INVALID"):
        assert FailureClassifier.classify(error_code=code) is RepairClass.B, code


def test_node_config_invalid_is_class_a_when_mechanical() -> None:
    result = FailureClassifier.classify(
        error_code="NODE_CONFIG_INVALID",
        error_details={"violations": [{"field": "method", "message": "required"}]},
    )
    assert result is RepairClass.A


def test_node_config_invalid_is_class_b_when_user_owned_value() -> None:
    result = FailureClassifier.classify(
        error_code="NODE_CONFIG_INVALID",
        error_details={"violations": [{"field": "url", "message": "must be a valid URL"}]},
    )
    assert result is RepairClass.B


def test_node_config_invalid_checks_message_too() -> None:
    result = FailureClassifier.classify(
        error_code="NODE_CONFIG_INVALID", message="missing credential for this node",
    )
    assert result is RepairClass.B


def test_unmapped_error_code_defaults_to_class_b_fail_safe() -> None:
    assert FailureClassifier.classify(error_code="SOME_FUTURE_CODE") is RepairClass.B


def test_empty_error_code_falls_back_to_keyword_scan_mechanical() -> None:
    assert FailureClassifier.classify(message="node X is missing a required field") is RepairClass.A


def test_empty_error_code_falls_back_to_keyword_scan_user_owned() -> None:
    assert FailureClassifier.classify(message="the credential could not be resolved") is RepairClass.B


def test_empty_error_code_and_no_keywords_defaults_to_class_b() -> None:
    assert FailureClassifier.classify(message="something went wrong") is RepairClass.B


def test_empty_everything_defaults_to_class_b() -> None:
    assert FailureClassifier.classify() is RepairClass.B


# --- SA-2132: nested-workflow failures are terminal Class B with a specific reason ---

_CYCLE_MSG = "Cross-workflow cycle detected: workflow 'wf-9' already appears in the ancestry chain"
_DEPTH_MSG = "Max nesting depth 10 exceeded at node 'subwf_1'"
_RECURSION_MSG = "Successor processing exceeded max recursion depth (25)"
_MISSING_TARGET_MSG = "WORKFLOW node 'subwf_1' missing config.workflow_id"


def test_cross_workflow_cycle_is_class_b() -> None:
    assert FailureClassifier.classify(message=_CYCLE_MSG) is RepairClass.B


def test_cycle_is_class_b_even_when_coded_as_a_validation_error() -> None:
    # The regression that matters: a cycle surfacing with a Class-A code must NOT be handed to
    # the repairer to loop on — the nested-workflow guard overrides the code table.
    assert FailureClassifier.classify(error_code="VALIDATION_ERROR", message=_CYCLE_MSG) is RepairClass.B


def test_max_nesting_depth_is_class_b() -> None:
    assert FailureClassifier.classify(message=_DEPTH_MSG) is RepairClass.B
    assert FailureClassifier.classify(message=_RECURSION_MSG) is RepairClass.B


def test_missing_sub_workflow_target_is_class_b() -> None:
    assert FailureClassifier.classify(message=_MISSING_TARGET_MSG) is RepairClass.B


def test_nested_workflow_reason_names_the_cycle() -> None:
    reason = FailureClassifier.nested_workflow_reason(message=_CYCLE_MSG)
    assert reason is not None
    assert "cycle" in reason.lower()
    assert "provide the correct value" not in reason.lower()
    assert "wf-9" in reason  # carries the platform's own detail


def test_nested_workflow_reason_names_the_depth() -> None:
    reason = FailureClassifier.nested_workflow_reason(message=_DEPTH_MSG)
    assert reason is not None
    assert "deep" in reason.lower() or "nesting" in reason.lower()


def test_nested_workflow_reason_names_the_missing_target() -> None:
    reason = FailureClassifier.nested_workflow_reason(message=_MISSING_TARGET_MSG)
    assert reason is not None
    assert "sub-workflow" in reason.lower() and "invoke" in reason.lower()


def test_nested_workflow_reason_is_none_for_unrelated_failures() -> None:
    assert FailureClassifier.nested_workflow_reason(message="node X is missing a required field") is None
    assert FailureClassifier.nested_workflow_reason(error_code="VALIDATION_ERROR", message="bad edge") is None
