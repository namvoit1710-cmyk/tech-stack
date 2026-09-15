import logging
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer4_frameworks.providers.http_result_reporter import HttpResultReporter
from app.layer4_frameworks.providers.http_worker_executor import HttpWorkerExecutor


# --- HttpResultReporter ---

def _mock_httpx_client(json_response=None):
    mock_response = MagicMock()
    mock_response.json.return_value = json_response or {"ok": True}
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    return mock_client


@patch("app.layer4_frameworks.providers.http_result_reporter.httpx.Client")
def test_report_completed(mock_client_cls):
    mock_client = _mock_httpx_client({"status": "completed"})
    mock_client_cls.return_value = mock_client

    reporter = HttpResultReporter("http://localhost:8001/")
    result = reporter.report_completed(
        run_id="r1",
        task_id="t1",
        output_reference="s3://bucket/out",
        output_metadata={"rows": 10},
    )
    assert result == {"status": "completed"}
    mock_client.post.assert_called_once()
    call_url = mock_client.post.call_args[0][0]
    assert "/runs/r1/tasks/t1/complete" in call_url


@patch("app.layer4_frameworks.providers.http_result_reporter.httpx.Client")
def test_report_failed(mock_client_cls):
    mock_client = _mock_httpx_client({"status": "failed"})
    mock_client_cls.return_value = mock_client

    reporter = HttpResultReporter("http://localhost:8001")
    result = reporter.report_failed(
        run_id="r1",
        task_id="t1",
        error={"code": "WORKER_ERROR", "message": "boom"},
    )
    assert result == {"status": "failed"}
    call_url = mock_client.post.call_args[0][0]
    assert "/runs/r1/tasks/t1/fail" in call_url


@patch("app.layer4_frameworks.providers.http_result_reporter.httpx.Client")
def test_report_completed_does_not_log_full_body(mock_client_cls, caplog):
    """SA-1941 — the reporter must log a SIZE SUMMARY, never the output_metadata
    body (which can be a large inline worker output). The data is still POSTed."""
    mock_client = _mock_httpx_client({"status": "completed"})
    mock_client_cls.return_value = mock_client
    resp_marker = "WCP_RESPONSE_BODY_MARKER"
    mock_client.post.return_value.text = resp_marker  # the WCP response body

    marker = "SENSITIVE_INLINE_OUTPUT_" + "x" * 20000  # a big request-body value
    reporter = HttpResultReporter("http://localhost:8001/")
    with caplog.at_level(logging.INFO):
        reporter.report_completed(
            run_id="r1", task_id="t1", output_metadata={"blob": marker},
        )

    # Neither the request body NOR the WCP response body appears in the logs.
    assert marker not in caplog.text
    assert resp_marker not in caplog.text
    # ...but a field-count summary is logged...
    assert "output_metadata_fields=1" in caplog.text
    # ...and the data was still delivered to the WCP.
    posted = mock_client.post.call_args[1]["json"]
    assert posted["output_metadata"]["blob"] == marker


@patch("app.layer4_frameworks.providers.http_result_reporter.httpx.Client")
def test_report_failed_does_not_log_outputs(mock_client_cls, caplog):
    """SA-1941 — report_failed logs an error-code + outputs field-count summary,
    never the (possibly large) outputs body; the data is still POSTed."""
    mock_client = _mock_httpx_client({"status": "failed"})
    mock_client_cls.return_value = mock_client

    marker = "FAIL_OUTPUT_" + "z" * 20000
    reporter = HttpResultReporter("http://localhost:8001")
    with caplog.at_level(logging.INFO):
        reporter.report_failed(
            run_id="r1", task_id="t1",
            error={"code": "WORKER_ERROR", "message": "boom"},
            outputs={"blob": marker},
        )

    assert marker not in caplog.text
    assert "error_code=WORKER_ERROR" in caplog.text
    posted = mock_client.post.call_args[1]["json"]
    assert posted["outputs"]["blob"] == marker


def test_kafka_publisher_does_not_log_full_result(caplog):
    """SA-1941 — the Kafka result publisher logs topic + task_id, never the full
    result payload (large inline output); the data is still published in full."""
    from app.layer4_frameworks.messaging.kafka_result_publisher import KafkaResultPublisher

    client = MagicMock()
    pub = KafkaResultPublisher(client, topic="workflow.results")
    marker = "BIG_RESULT_" + "y" * 20000
    # The REAL envelope callers publish is nested — task_id lives under payload.
    envelope = {
        "message_type": "task.completed",
        "correlation_id": "r1",
        "payload": {"task_id": "t9", "outputs": {"blob": marker}},
    }
    with caplog.at_level(logging.INFO):
        pub.publish_result(envelope)

    assert marker not in caplog.text                 # LOG_FULL_PAYLOAD guard
    assert "task_id=t9" in caplog.text                # id extracted from NESTED payload
    client.publish_to_topic.assert_called_once()
    _, payload = client.publish_to_topic.call_args[0]
    assert payload["payload"]["outputs"]["blob"] == marker  # full data still published


# --- HttpWorkerExecutor ---

@patch("app.layer4_frameworks.providers.http_worker_executor.httpx.Client")
def test_http_worker_executor_execute(mock_client_cls):
    mock_client = _mock_httpx_client({"output": {"uri": "s3://result"}, "metrics": {"duration_ms": 100}})
    mock_client_cls.return_value = mock_client

    executor = HttpWorkerExecutor("http://localhost:9000/")
    result = executor.execute({
        "task_id": "t1",
        "action": "execute",
        "inputs": {"key": "value"},
        "parameters": {"timeout": 30},
        "correlation_id": "run-123",
    })
    assert result["output"]["uri"] == "s3://result"
    call_url = mock_client.post.call_args[0][0]
    assert call_url == "http://localhost:9000/api/v1/execute"
    posted_json = mock_client.post.call_args[1]["json"]
    assert posted_json["task_id"] == "t1"
    assert posted_json["action"] == "execute"
    assert posted_json["inputs"] == {"key": "value"}
    assert posted_json["parameters"] == {"timeout": 30}
    assert posted_json["correlation_id"] == "run-123"
