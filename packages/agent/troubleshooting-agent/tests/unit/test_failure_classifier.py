"""Core: the deterministic failure classifier maps each execution-log signal to the
right FailureCategory. If this breaks, the agent's whole verdict is wrong."""
from app.layer1_domain.services.failure_classifier import classify
from app.layer1_domain.value_objects.failure_category import FailureCategory


def _log(error_code="", error_message="", failed=1, started=True, with_node=True):
    events = []
    if started:
        events.append({"event_type": "RunStatusChanged", "payload": {"status": "started"}})
    if with_node:
        events.append({
            "event_type": "NodeFailed",
            "payload": {"node_id": "task-1", "error_code": error_code,
                        "error_message": error_message},
        })
    return {"summary": {"node_counts": {"failed": failed}}, "events": events}


def test_agent_service_communication_http_500():
    r = classify(_log("HTTP_500", "HTTP 500"), "run-1")
    assert r.category is FailureCategory.AGENT_SERVICE_COMMUNICATION
    assert "HTTP 500" in r.root_cause
    assert r.recommended_fix
    assert r.failed_node_ids == ["task-1"]
    assert r.confidence >= 0.8


def test_step_execution_expression_error():
    r = classify(_log("ExpressionError", "Expression error: $.x is undefined"))
    assert r.category is FailureCategory.STEP_EXECUTION


def test_creation_configuration_not_configured():
    r = classify(_log("", "CSV converter or file service not configured"))
    assert r.category is FailureCategory.CREATION_CONFIGURATION


def test_timeout_retry():
    r = classify(_log("", "Task timed out after 30s"))
    assert r.category is FailureCategory.TIMEOUT_RETRY


def test_state_management():
    r = classify(_log("", "stale aggregate; state mismatch on replay"))
    assert r.category is FailureCategory.STATE_MANAGEMENT


def test_triggering_when_run_never_started():
    # Failed run, no node-level failure, never reached a started state.
    log = {"summary": {"node_counts": {"failed": 1}}, "events": []}
    r = classify(log, "run-x")
    assert r.category is FailureCategory.TRIGGERING


def test_unknown_when_no_signal_matches():
    r = classify(_log("WeirdCode", "something odd happened"))
    assert r.category is FailureCategory.UNKNOWN
    assert r.confidence < 0.5


def test_no_failure_on_healthy_run():
    log = {"summary": {"node_counts": {"failed": 0}},
           "events": [{"event_type": "RunStatusChanged", "payload": {"status": "started"}}]}
    r = classify(log, "run-ok")
    assert r.category is FailureCategory.NO_FAILURE
    assert r.severity == "none"
    assert r.confidence == 1.0
