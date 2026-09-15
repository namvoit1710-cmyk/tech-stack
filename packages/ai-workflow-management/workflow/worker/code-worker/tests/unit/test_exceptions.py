from app.layer4_frameworks.exceptions import (
    CodeRuntimeError,
    CodeTimeoutError,
    CodeWorkerError,
    UnsupportedLanguageError,
)


class TestExceptions:
    def test_hierarchy(self):
        assert issubclass(CodeTimeoutError, CodeWorkerError)
        assert issubclass(CodeRuntimeError, CodeWorkerError)
        assert issubclass(UnsupportedLanguageError, CodeWorkerError)

    def test_raisable(self):
        for exc in (CodeTimeoutError, CodeRuntimeError, UnsupportedLanguageError):
            try:
                raise exc("boom")
            except CodeWorkerError as e:
                assert "boom" in str(e)
