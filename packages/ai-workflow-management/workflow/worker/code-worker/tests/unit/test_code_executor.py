"""Integration tests for SubprocessCodeExecutor (spawns a real child process)."""

import importlib.util

import pytest

from app.layer4_frameworks.providers.subprocess_code_executor import SubprocessCodeExecutor

_HAS_MINI_RACER = importlib.util.find_spec("py_mini_racer") is not None
requires_js = pytest.mark.skipif(
    not _HAS_MINI_RACER, reason="py_mini_racer not installed"
)


@pytest.fixture
def executor():
    return SubprocessCodeExecutor()


class TestPythonViaSubprocess:
    def test_success(self, executor):
        res = executor.run(
            language="python",
            code="return [i['n'] * 2 for i in items]",
            items=[{"n": 1}, {"n": 2}],
            input_data={"items": [{"n": 1}, {"n": 2}]},
            mode="all_items",
            timeout_seconds=10,
        )
        assert res.success is True
        assert res.result == [2, 4]

    def test_logs_captured(self, executor):
        res = executor.run(
            language="python", code="print('from child')\nreturn 1",
            items=[], input_data={}, mode="all_items", timeout_seconds=10,
        )
        assert res.success is True
        assert res.logs == ["from child"]

    def test_blocked_import(self, executor):
        res = executor.run(
            language="python", code="import socket\nreturn 1",
            items=[], input_data={}, mode="all_items", timeout_seconds=10,
        )
        assert res.success is False
        assert "not allowed" in res.error

    def test_timeout(self, executor):
        # SA-1952 — the fixture now carries a (never-reached) ``return``. The
        # return-guard rejects a body with NO return before it runs, so the old
        # `while True: pass` fixture would be refused up front and never reach the
        # timeout it exists to exercise. The intent of the test is unchanged: an
        # infinite loop must be killed by the wall-clock/CPU limit.
        res = executor.run(
            language="python", code="while True:\n    pass\nreturn 1",
            items=[], input_data={}, mode="all_items", timeout_seconds=2,
        )
        assert res.success is False
        assert "timed out" in res.error.lower() or "cpu" in res.error.lower()

    def test_unsupported_language(self, executor):
        res = executor.run(
            language="ruby", code="puts 1",
            items=[], input_data={}, mode="all_items", timeout_seconds=5,
        )
        assert res.success is False


@requires_js
class TestJavaScriptViaSubprocess:
    def test_success(self, executor):
        res = executor.run(
            language="javascript",
            code="return items.map(function(i){ return i.n * 2; });",
            items=[{"n": 1}, {"n": 3}],
            input_data={"items": [{"n": 1}, {"n": 3}]},
            mode="all_items",
            timeout_seconds=10,
        )
        assert res.success is True
        assert res.result == [2, 6]

    def test_console_log_captured(self, executor):
        res = executor.run(
            language="javascript", code="console.log('hi', 42); return null;",
            items=[], input_data={}, mode="all_items", timeout_seconds=10,
        )
        assert res.success is True
        assert res.logs == ["hi 42"]

    def test_runtime_error(self, executor):
        res = executor.run(
            language="javascript", code="throw new Error('boom');",
            items=[], input_data={}, mode="all_items", timeout_seconds=10,
        )
        assert res.success is False
        assert "boom" in res.error
