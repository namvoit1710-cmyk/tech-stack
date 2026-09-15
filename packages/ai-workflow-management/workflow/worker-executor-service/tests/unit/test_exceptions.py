import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer4_frameworks.config.exceptions import AppError, TaskExecutionError, WorkerNotFoundError, RetryExhaustedError


def test_app_error_is_exception():
    err = AppError("base error")
    assert isinstance(err, Exception)
    assert str(err) == "base error"


def test_task_execution_error_inherits_app_error():
    err = TaskExecutionError("exec failed")
    assert isinstance(err, AppError)
    assert str(err) == "exec failed"


def test_worker_not_found_error_inherits_app_error():
    err = WorkerNotFoundError("no worker")
    assert isinstance(err, AppError)


def test_retry_exhausted_error_inherits_app_error():
    err = RetryExhaustedError("retries done")
    assert isinstance(err, AppError)
