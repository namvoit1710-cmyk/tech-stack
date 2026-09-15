from worker_sdk.layer1_domain.exceptions import (
    WorkerError,
    RegistrationError,
    HeartbeatError,
    TaskExecutionError,
    DataReadError,
    DataWriteError,
)


def test_worker_error_is_exception():
    err = WorkerError("base error")
    assert isinstance(err, Exception)
    assert str(err) == "base error"


def test_registration_error_inherits_worker_error():
    err = RegistrationError("reg failed")
    assert isinstance(err, WorkerError)
    assert isinstance(err, Exception)
    assert str(err) == "reg failed"


def test_heartbeat_error_inherits_worker_error():
    err = HeartbeatError("hb failed")
    assert isinstance(err, WorkerError)
    assert str(err) == "hb failed"


def test_task_execution_error_inherits_worker_error():
    err = TaskExecutionError("exec failed")
    assert isinstance(err, WorkerError)
    assert str(err) == "exec failed"


def test_data_read_error_inherits_worker_error():
    err = DataReadError("read failed")
    assert isinstance(err, WorkerError)
    assert str(err) == "read failed"


def test_data_write_error_inherits_worker_error():
    err = DataWriteError("write failed")
    assert isinstance(err, WorkerError)
    assert str(err) == "write failed"
