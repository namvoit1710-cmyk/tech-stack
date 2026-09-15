from app.layer4_frameworks.exceptions import WaitWorkerError, WaitTimeoutError, WaitCancelledError


class TestExceptions:
    def test_base_exception(self):
        err = WaitWorkerError("base error")
        assert str(err) == "base error"
        assert isinstance(err, Exception)

    def test_timeout_exception(self):
        err = WaitTimeoutError("timed out")
        assert str(err) == "timed out"
        assert isinstance(err, WaitWorkerError)

    def test_cancelled_exception(self):
        err = WaitCancelledError("cancelled")
        assert str(err) == "cancelled"
        assert isinstance(err, WaitWorkerError)
