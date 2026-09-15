"""Unit tests for SubprocessCodeExecutor's pure helpers + envelope decoding.

The full ``run()`` path spawns a real child process and is exercised as an
integration test in test_code_executor.py. This module unit-tests the pure,
process-free logic that test does not reach:
  * ``_describe_failure`` signal/exit-code -> user message (lines 141-150)
  * the stdout-envelope decode branches in ``run`` via a stubbed Popen
    (malformed JSON, non-zero exit, failed spawn) — the harness-mockable seams.

``subprocess_code_executor.subprocess`` / ``_IS_POSIX`` are the mock seams.
Everything is sync -> MagicMock (no AsyncMock).
"""

from unittest.mock import MagicMock

import pytest

import app.layer4_frameworks.providers.subprocess_code_executor as sce
from app.layer4_frameworks.providers.subprocess_code_executor import SubprocessCodeExecutor


# -- _describe_failure (static, pure) -----------------------------------------

class TestDescribeFailure:
    def test_non_posix_nonzero_uses_stderr(self, monkeypatch):
        # failure path (Windows/else branch, line 150): stderr text wins.
        monkeypatch.setattr(sce, "_IS_POSIX", False)
        msg = SubprocessCodeExecutor._describe_failure(1, "traceback boom", 30)
        assert msg == "traceback boom"

    def test_non_posix_nonzero_empty_stderr_reports_code(self, monkeypatch):
        # boundary: no stderr -> falls back to "runner exited with code N".
        monkeypatch.setattr(sce, "_IS_POSIX", False)
        msg = SubprocessCodeExecutor._describe_failure(3, "   ", 30)
        assert "runner exited with code 3" in msg

    def test_negative_code_on_non_posix_still_uses_else(self, monkeypatch):
        # edge: negative returncode but non-POSIX -> the signal branch is guarded
        # by ``_IS_POSIX`` (line 141), so it must NOT be interpreted as a signal.
        monkeypatch.setattr(sce, "_IS_POSIX", False)
        msg = SubprocessCodeExecutor._describe_failure(-9, "", 30)
        assert "runner exited with code -9" in msg

    def test_posix_sigxcpu_reports_cpu_limit(self, monkeypatch):
        # failure path (POSIX, line 145-146): SIGXCPU -> CPU time limit message.
        import signal
        monkeypatch.setattr(sce, "_IS_POSIX", True)
        rc = -getattr(signal, "SIGXCPU", 24)
        msg = SubprocessCodeExecutor._describe_failure(rc, "", 30)
        assert "CPU time limit" in msg
        assert "30" in msg

    def test_posix_sigkill_reports_timeout(self, monkeypatch):
        # failure path (POSIX, line 147-148): SIGKILL -> timeout message.
        import signal
        monkeypatch.setattr(sce, "_IS_POSIX", True)
        rc = -getattr(signal, "SIGKILL", 9)
        msg = SubprocessCodeExecutor._describe_failure(rc, "", 20)
        assert "timed out after 20s" in msg

    def test_posix_other_signal_uses_stderr_then_signal_num(self, monkeypatch):
        # edge: an unmapped signal -> stderr if present, else "terminated by signal".
        monkeypatch.setattr(sce, "_IS_POSIX", True)
        assert SubprocessCodeExecutor._describe_failure(-2, "detail", 30) == "detail"
        msg = SubprocessCodeExecutor._describe_failure(-2, "", 30)
        assert "terminated by signal 2" in msg


# -- run() envelope decoding via stubbed Popen --------------------------------

class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    def communicate(self, input=None, timeout=None):
        return self._stdout, self._stderr


def _patch_popen(monkeypatch, proc):
    fake_subprocess = MagicMock()
    fake_subprocess.Popen.return_value = proc
    # Preserve the real TimeoutExpired type so the except clause still matches.
    fake_subprocess.TimeoutExpired = sce.subprocess.TimeoutExpired
    fake_subprocess.PIPE = sce.subprocess.PIPE
    monkeypatch.setattr(sce, "subprocess", fake_subprocess)


class TestRunEnvelopeDecoding:
    def test_happy_decodes_ok_envelope(self, monkeypatch):
        # happy: valid JSON stdout -> success result mapped from the envelope.
        proc = _FakeProc(
            stdout='{"ok": true, "result": [1, 2], "logs": ["hi"], "error": null}',
            returncode=0,
        )
        _patch_popen(monkeypatch, proc)
        res = SubprocessCodeExecutor().run(
            "python", "return 1", [], {}, "all_items", 10
        )
        assert res.success is True
        assert res.result == [1, 2]
        assert res.logs == ["hi"]

    def test_malformed_stdout_reports_stderr(self, monkeypatch):
        # failure path (lines 120-122): unparseable stdout -> stderr message.
        proc = _FakeProc(stdout="not-json", stderr="boom detail", returncode=0)
        _patch_popen(monkeypatch, proc)
        res = SubprocessCodeExecutor().run(
            "python", "return 1", [], {}, "all_items", 10
        )
        assert res.success is False
        assert res.error == "boom detail"

    def test_malformed_stdout_empty_stderr_default_message(self, monkeypatch):
        # boundary: malformed stdout AND empty stderr -> generic malformed message.
        proc = _FakeProc(stdout="", stderr="", returncode=0)
        _patch_popen(monkeypatch, proc)
        res = SubprocessCodeExecutor().run(
            "python", "return 1", [], {}, "all_items", 10
        )
        # returncode 0 + empty stdout -> json.loads("") fails -> malformed branch.
        assert res.success is False
        assert "malformed output" in res.error

    def test_nonzero_exit_no_stdout_describes_failure(self, monkeypatch):
        # failure path (lines 114-116): nonzero rc + empty stdout -> _describe_failure.
        monkeypatch.setattr(sce, "_IS_POSIX", False)
        proc = _FakeProc(stdout="", stderr="crash trace", returncode=1)
        _patch_popen(monkeypatch, proc)
        res = SubprocessCodeExecutor().run(
            "python", "return 1", [], {}, "all_items", 10
        )
        assert res.success is False
        assert res.error == "crash trace"

    def test_timeout_kills_and_reports(self, monkeypatch):
        # failure path (lines 106-112): communicate raises TimeoutExpired.
        class _TimeoutProc(_FakeProc):
            def __init__(self):
                super().__init__()
                self._calls = 0

            def communicate(self, input=None, timeout=None):
                self._calls += 1
                if self._calls == 1:
                    raise sce.subprocess.TimeoutExpired(cmd="runner", timeout=timeout)
                return "", ""  # second call after _kill()

        proc = _TimeoutProc()
        _patch_popen(monkeypatch, proc)
        # _kill touches proc.kill() on non-POSIX; give it one.
        monkeypatch.setattr(sce, "_IS_POSIX", False)
        proc.kill = MagicMock()
        res = SubprocessCodeExecutor().run(
            "python", "while True: pass", [], {}, "all_items", 5
        )
        assert res.success is False
        assert "timed out after 5s" in res.error
        proc.kill.assert_called_once()

    def test_spawn_failure_reported(self, monkeypatch):
        # failure path (lines 97-98): Popen itself raises -> "Failed to start runner".
        fake_subprocess = MagicMock()
        fake_subprocess.Popen.side_effect = OSError("no exe")
        fake_subprocess.TimeoutExpired = sce.subprocess.TimeoutExpired
        fake_subprocess.PIPE = sce.subprocess.PIPE
        monkeypatch.setattr(sce, "subprocess", fake_subprocess)
        res = SubprocessCodeExecutor().run(
            "python", "return 1", [], {}, "all_items", 10
        )
        assert res.success is False
        assert "Failed to start runner" in res.error


# -- _kill (static, swallows lookup errors) -----------------------------------

class TestKill:
    def test_kill_non_posix_calls_proc_kill(self, monkeypatch):
        # non-POSIX: proc.kill() is invoked.
        monkeypatch.setattr(sce, "_IS_POSIX", False)
        proc = MagicMock()
        SubprocessCodeExecutor._kill(proc)
        proc.kill.assert_called_once()

    def test_kill_swallows_process_lookup_error(self, monkeypatch):
        # failure path: a dead process (ProcessLookupError) must not propagate.
        monkeypatch.setattr(sce, "_IS_POSIX", False)
        proc = MagicMock()
        proc.kill.side_effect = ProcessLookupError()
        # Must not raise.
        SubprocessCodeExecutor._kill(proc)
