from __future__ import annotations

from app.layer1_domain.entities.repair_log import RepairAttempt, RepairLog


def test_empty_log_has_no_attempts() -> None:
    log = RepairLog()
    assert log.attempt_count == 0
    assert log.last_error() == ""
    assert log.to_document() == []


def test_record_appends_and_documents_each_attempt() -> None:
    log = RepairLog()
    log.record(RepairAttempt(attempt=1, error_code="VALIDATION_ERROR",
                              error_message="bad graph", repair_class="A", fixed=True))
    log.record(RepairAttempt(attempt=2, error_code="NODE_CONFIG_INVALID",
                              error_message="missing url", repair_class="B", fixed=False))

    assert log.attempt_count == 2
    assert log.last_error() == "missing url"
    document = log.to_document()
    assert document == [
        {"attempt": 1, "error_code": "VALIDATION_ERROR", "error_message": "bad graph",
         "repair_class": "A", "fixed": True},
        {"attempt": 2, "error_code": "NODE_CONFIG_INVALID", "error_message": "missing url",
         "repair_class": "B", "fixed": False},
    ]
