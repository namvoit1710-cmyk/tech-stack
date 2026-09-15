from app.layer4_frameworks.exceptions import HttpRequestWorkerError, HttpRequestTimeoutError, HttpRequestConnectionError


class TestExceptions:
    def test_base_exception(self):
        err = HttpRequestWorkerError("base")
        assert str(err) == "base"
        assert isinstance(err, Exception)

    def test_timeout_exception(self):
        err = HttpRequestTimeoutError("timed out")
        assert isinstance(err, HttpRequestWorkerError)

    def test_connection_exception(self):
        err = HttpRequestConnectionError("conn failed")
        assert isinstance(err, HttpRequestWorkerError)
