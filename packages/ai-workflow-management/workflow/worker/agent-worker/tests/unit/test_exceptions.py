import pytest

from app.layer4_frameworks.exceptions import (
    AgentWorkerError,
    AiProviderAuthenticationError,
    AiProviderConnectionError,
    AiProviderRateLimitError,
    AiProviderResponseError,
)


class TestExceptionHierarchy:
    def test_base_exception(self):
        exc = AgentWorkerError("base")
        assert isinstance(exc, Exception)
        assert str(exc) == "base"

    def test_connection_error_inherits(self):
        exc = AiProviderConnectionError("conn failed")
        assert isinstance(exc, AgentWorkerError)
        assert str(exc) == "conn failed"

    def test_authentication_error_inherits(self):
        exc = AiProviderAuthenticationError("bad key")
        assert isinstance(exc, AgentWorkerError)

    def test_rate_limit_error_inherits(self):
        exc = AiProviderRateLimitError("too many")
        assert isinstance(exc, AgentWorkerError)

    def test_response_error_inherits(self):
        exc = AiProviderResponseError("unexpected")
        assert isinstance(exc, AgentWorkerError)

    def test_can_catch_all_with_base(self):
        exceptions = [
            AiProviderConnectionError("a"),
            AiProviderAuthenticationError("b"),
            AiProviderRateLimitError("c"),
            AiProviderResponseError("d"),
        ]
        for exc in exceptions:
            with pytest.raises(AgentWorkerError):
                raise exc
