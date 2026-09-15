"""The code node must not be able to kill the worker.

Measured 2026-07-14 in a real 256 MB cgroup (the CF container's shape): the runner
child had no memory cap, and the parent holds a result at ~5-6x its serialized size,
so a few tens of MB of code-node output — one big node, or a handful of ordinary ones
at once — SIGKILLed the worker and CF restarted the instance.

These tests pin the three ceilings that stop that, plus the switch away from
``preexec_fn`` (unsafe to fork with from the SDK's thread pool).
"""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from app.layer4_frameworks.providers import subprocess_code_executor as sce
from app.layer4_frameworks.providers.runner import __main__ as runner_main
from app.layer4_frameworks.providers.runner import limits
from app.layer4_frameworks.providers.runner.python_runner import (
    _ensure_serializable,
    run_python,
)

POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="setrlimit/process groups are POSIX-only"
)


# --- the limits themselves ---------------------------------------------------------

class TestLimitConfig:
    def test_reads_the_environment(self, monkeypatch):
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "7")
        assert limits.limit_mb("CODE_NODE_MAX_OUTPUT_MB", 24) == 7

    def test_falls_back_to_the_default_when_unset_or_junk(self, monkeypatch):
        monkeypatch.delenv("CODE_NODE_MAX_OUTPUT_MB", raising=False)
        assert limits.limit_mb("CODE_NODE_MAX_OUTPUT_MB", 24) == 24
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "not-a-number")
        assert limits.limit_mb("CODE_NODE_MAX_OUTPUT_MB", 24) == 24

    def test_zero_disables_and_negative_cannot_re_enable(self, monkeypatch):
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "0")
        assert limits.limit_mb("CODE_NODE_MAX_OUTPUT_MB", 24) == 0
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "-5")
        assert limits.limit_mb("CODE_NODE_MAX_OUTPUT_MB", 24) == 0

class TestDerivedFromThePod:
    """The ceilings are a consequence of the pod, not a number someone typed.

    A hard-coded "24 MB" becomes a lie the moment anyone edits `memory:` in
    deployment.yml or changes the concurrency. These pin the arithmetic instead.
    """

    def test_what_a_256mb_pod_actually_buys(self):
        # 256 pod - 96 baseline - 38 headroom = 122 usable, one script at a time.
        memory_mb, output_mb, effective = limits.derive_limits(
            pod_mb=256, baseline_mb=96, max_concurrent=1
        )
        assert (memory_mb, effective) == (122, 1)
        assert output_mb == 122 // limits.PARENT_AMPLIFICATION

    def test_a_bigger_pod_raises_the_ceilings_on_its_own(self):
        small, _, _ = limits.derive_limits(256, 96, 1)
        large, _, _ = limits.derive_limits(512, 96, 1)
        assert large > small * 2, "bumping `memory:` must move the ceilings with it"

    def test_concurrency_is_paid_for_out_of_the_ceilings(self):
        """Two scripts at once means each gets half the pod. That is the whole point:
        four concurrent nodes at 20 MB each — all individually legal — OOM-killed the
        worker before admission control existed."""
        one_mem, one_out, _ = limits.derive_limits(512, 96, 1)
        two_mem, two_out, eff = limits.derive_limits(512, 96, 2)
        assert eff == 2
        assert two_mem <= one_mem // 2 + 1
        assert two_out <= one_out // 2 + 1

    def test_concurrency_the_pod_cannot_afford_is_lowered_not_granted(self):
        """A 256 MB pod cannot run four scripts at once. Handing each of them a
        ceiling anyway would just move the OOM kill somewhere less obvious — so we
        run fewer scripts instead."""
        memory_mb, _, effective = limits.derive_limits(
            pod_mb=256, baseline_mb=96, max_concurrent=4
        )
        assert effective == 1
        assert 96 + memory_mb * effective <= 256

    def test_the_budget_always_fits_the_pod(self):
        """The invariant the whole fix rests on: what the code node may touch, times
        the number that may actually run at once, has to fit in what the worker is not
        already using. If this ever fails, the container dies."""
        baseline = 96
        for pod in (256, 384, 512, 1024):
            for requested in (1, 2, 4, 8):
                memory_mb, output_mb, effective = limits.derive_limits(
                    pod, baseline, requested
                )
                child_total = memory_mb * effective
                parent_total = output_mb * limits.PARENT_AMPLIFICATION * effective
                assert baseline + max(child_total, parent_total) <= pod, (
                    f"pod={pod} requested={requested} -> effective={effective}: "
                    f"the derived budget overruns the container"
                )

    def test_a_starved_pod_degrades_to_small_but_working_and_says_so(self):
        memory_mb, output_mb, effective = limits.derive_limits(
            pod_mb=128, baseline_mb=120, max_concurrent=4
        )
        assert (memory_mb, output_mb, effective) == (
            limits.MIN_MEMORY_MB, limits.MIN_OUTPUT_MB, 1
        )
        assert limits.pod_is_too_small(128, 120) is True
        assert limits.pod_is_too_small(256, 96) is False


class TestReadingThePodQuota:
    def test_prefers_cloud_foundrys_own_answer(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LIMIT", "512M")
        assert limits.pod_memory_mb() == 512

    def test_falls_back_to_vcap(self, monkeypatch):
        monkeypatch.delenv("MEMORY_LIMIT", raising=False)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"limits": {"mem": 384}}))
        assert limits.pod_memory_mb() == 384

    def test_junk_does_not_crash_the_worker(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LIMIT", "lots")
        monkeypatch.setenv("VCAP_APPLICATION", "not json")
        assert limits.pod_memory_mb() > 0


class TestExplicitOverride:
    def test_an_explicit_ceiling_beats_the_derivation(self, monkeypatch):
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "3")
        _, output_mb, _ = limits.resolve_limits(max_concurrent=1)
        assert output_mb == 3

    def test_the_enforced_budget_is_logged_not_hidden(self):
        line = limits.describe_budget(max_concurrent=1)
        for part in ("pod=", "worker_baseline=", "child_memory=", "output="):
            assert part in line


class TestUserFacingMessages:
    """A business user cannot see the container, the runner, or the OOM killer."""

    def test_memory_message_names_the_limit_and_what_to_do(self):
        msg = limits.memory_limit_message(160)
        assert "160 MB" in msg
        assert "fewer rows" in msg
        for jargon in ("RLIMIT", "OOM", "cgroup", "subprocess", "container"):
            assert jargon not in msg

    def test_output_message_reports_the_actual_size(self):
        msg = limits.output_limit_message(61 * 1024 * 1024, 24)
        assert "61.0 MB" in msg
        assert "24 MB" in msg


# --- the fork itself ---------------------------------------------------------------

class TestSpawn:
    def _captured_kwargs(self, monkeypatch) -> dict:
        seen: dict = {}

        class _FakeProc:
            returncode = 0

            def communicate(self, input=None, timeout=None):  # noqa: A002
                return json.dumps({"ok": True, "result": [], "logs": []}), ""

        def _fake_popen(args, **kwargs):
            seen.update(kwargs)
            return _FakeProc()

        monkeypatch.setattr(sce.subprocess, "Popen", _fake_popen)
        sce.SubprocessCodeExecutor().run(
            language="python", code="return 1", items=[], input_data={},
            mode="all_items", timeout_seconds=5,
        )
        return seen

    def test_does_not_use_preexec_fn(self, monkeypatch):
        """preexec_fn runs Python in the forked child before exec.

        ``exec_uc.execute`` is dispatched with ``asyncio.to_thread``, so this Popen
        happens on a worker thread of a multi-threaded process — exactly the case
        CPython documents as unsafe: the child can deadlock before exec, and
        ``Popen()`` blocks until exec completes, so the calling thread would hang
        forever with no timeout to rescue it. Don't bring it back.
        """
        kwargs = self._captured_kwargs(monkeypatch)
        assert kwargs.get("preexec_fn") is None

    def test_starts_a_new_session_so_the_tree_can_be_killed(self, monkeypatch):
        kwargs = self._captured_kwargs(monkeypatch)
        assert kwargs.get("start_new_session") is True

    def test_child_is_told_not_to_write_bytecode(self, monkeypatch):
        # The child sets RLIMIT_FSIZE=0; a stray .pyc write would look like a failure.
        kwargs = self._captured_kwargs(monkeypatch)
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


class TestLimitsReachTheChild:
    def test_job_payload_carries_the_ceilings(self, monkeypatch):
        monkeypatch.setenv("CODE_NODE_MAX_MEMORY_MB", "111")
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "9")
        sent: dict = {}

        class _FakeProc:
            returncode = 0

            def communicate(self, input=None, timeout=None):  # noqa: A002
                sent.update(json.loads(input))
                return json.dumps({"ok": True, "result": 1, "logs": []}), ""

        monkeypatch.setattr(sce.subprocess, "Popen", lambda a, **k: _FakeProc())
        sce.SubprocessCodeExecutor().run(
            language="python", code="return 1", items=[], input_data={},
            mode="all_items", timeout_seconds=5,
        )
        assert sent["max_memory_mb"] == 111
        assert sent["max_output_mb"] == 9


class TestFailureReporting:
    def test_a_hard_memory_death_is_explained_not_leaked(self):
        msg = sce.SubprocessCodeExecutor._describe_failure(
            returncode=1, stderr="MemoryError\n", timeout_seconds=30, max_memory_mb=160
        )
        assert "ran out of memory" in msg
        assert "160 MB" in msg

    def test_a_timeout_still_reads_as_a_timeout(self):
        msg = sce.SubprocessCodeExecutor._describe_failure(
            returncode=1, stderr="", timeout_seconds=30
        )
        assert "code 1" in msg or "timed out" in msg


class TestConcurrencyGate:
    def test_only_max_concurrent_scripts_run_at_once(self, monkeypatch):
        """The per-task ceilings bound one script; this bounds the container.

        Four concurrent nodes returning 20 MB each — every one of them under the
        output ceiling — still OOM-killed the worker before this gate existed.
        """
        monkeypatch.setattr(sce, "_SLOTS", threading.BoundedSemaphore(2))
        live = 0
        peak = 0
        lock = threading.Lock()

        def _slow_run(*a, **k):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.05)
            with lock:
                live -= 1
            return sce.CodeExecutionResult(success=True, result=None)

        monkeypatch.setattr(sce.SubprocessCodeExecutor, "_run", _slow_run)
        ex = sce.SubprocessCodeExecutor()
        threads = [
            threading.Thread(
                target=lambda: ex.run("python", "return 1", [], {}, "all_items", 5)
            )
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert peak <= 2, f"{peak} scripts ran at once; the gate allows 2"

    def test_the_slot_is_released_even_when_a_run_explodes(self, monkeypatch):
        monkeypatch.setattr(sce, "_SLOTS", threading.BoundedSemaphore(1))

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(sce.SubprocessCodeExecutor, "_run", _boom)
        ex = sce.SubprocessCodeExecutor()
        for _ in range(3):
            with pytest.raises(RuntimeError):
                ex.run("python", "return 1", [], {}, "all_items", 5)
        # A leaked slot would make this block forever.
        assert sce._SLOTS.acquire(timeout=1) is True
        sce._SLOTS.release()


class TestChildIsNotOrphaned:
    def test_an_unexpected_error_while_reading_still_kills_the_child(self, monkeypatch):
        killed: list = []

        class _FakeProc:
            returncode = 0
            pid = 4242

            def communicate(self, input=None, timeout=None):  # noqa: A002
                raise KeyboardInterrupt

        monkeypatch.setattr(sce.subprocess, "Popen", lambda a, **k: _FakeProc())
        monkeypatch.setattr(
            sce.SubprocessCodeExecutor, "_kill", staticmethod(lambda p: killed.append(p))
        )
        with pytest.raises(KeyboardInterrupt):
            sce.SubprocessCodeExecutor().run(
                "python", "return 1", [], {}, "all_items", 5
            )
        assert killed, "the child was left running with the pipes open"


# --- the output ceiling ------------------------------------------------------------

class TestOutputCeiling:
    def test_measure_counts_without_building_the_string(self):
        payload = {"ok": True, "result": [1, 2, 3], "logs": []}
        assert runner_main._measure(payload) == len(json.dumps(payload))

    def test_under_the_ceiling_the_result_passes_through(self, capsys):
        payload = {"ok": True, "result": [{"a": 1}], "logs": ["hi"]}
        runner_main._emit(payload, max_output_bytes=1024)
        assert json.loads(capsys.readouterr().out) == payload

    def test_over_the_ceiling_nothing_big_is_emitted(self, capsys):
        payload = {"ok": True, "result": ["x" * 5000], "logs": ["a", "b"]}
        runner_main._emit(payload, max_output_bytes=1024)
        out = json.loads(capsys.readouterr().out)

        assert out["ok"] is False
        assert out["result"] is None          # the parent must never see it
        assert "over the 0 MB limit" in out["error"] or "MB limit" in out["error"]
        assert out["logs"] == ["a", "b"]      # the user's own prints survive

    def test_zero_disables_the_ceiling(self, capsys):
        payload = {"ok": True, "result": ["x" * 5000], "logs": []}
        runner_main._emit(payload, max_output_bytes=0)
        assert json.loads(capsys.readouterr().out)["ok"] is True


class TestSerializableCheckIsCheap:
    def test_still_coerces_what_json_cannot_encode(self):
        assert _ensure_serializable({1, 2}) == repr({1, 2})

    def test_leaves_plain_data_untouched(self):
        value = [{"sku": "A", "qty": 1}]
        assert _ensure_serializable(value) is value

    def test_memory_error_is_not_swallowed_as_a_script_error(self, monkeypatch):
        """The runner entrypoint owns MemoryError: only it knows the ceiling.

        Reported as a bare "MemoryError:" it tells the user nothing.
        """
        def _boom(_self, _o):
            raise MemoryError

        monkeypatch.setattr(json.JSONEncoder, "iterencode", _boom)
        with pytest.raises(MemoryError):
            run_python("return [1]", [], {}, "all_items")


# --- end to end, through a real subprocess ------------------------------------------

class TestEndToEnd:
    def test_an_oversized_result_fails_cleanly_instead_of_killing_the_worker(
        self, monkeypatch
    ):
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "1")
        result = sce.SubprocessCodeExecutor().run(
            language="python",
            code='return [{"i": i, "pad": "x"*80} for i in range(40000)]',
            items=[], input_data={}, mode="all_items", timeout_seconds=60,
        )
        assert result.success is False
        assert result.result is None
        assert "over the 1 MB limit" in (result.error or "")

    def test_a_result_under_the_ceiling_is_returned_normally(self, monkeypatch):
        monkeypatch.setenv("CODE_NODE_MAX_OUTPUT_MB", "8")
        result = sce.SubprocessCodeExecutor().run(
            language="python",
            code='return [{"i": i} for i in range(1000)]',
            items=[], input_data={}, mode="all_items", timeout_seconds=60,
        )
        assert result.success is True
        assert len(result.result) == 1000

    @POSIX_ONLY
    def test_a_runaway_allocation_dies_in_the_child_not_in_the_container(
        self, monkeypatch
    ):
        """Before RLIMIT_AS this allocated 700 MB inside a 256 MB container and the
        kernel OOM-killer took the *worker* down with it."""
        monkeypatch.setenv("CODE_NODE_MAX_MEMORY_MB", "128")
        result = sce.SubprocessCodeExecutor().run(
            language="python",
            code="x = bytearray(400 * 1024 * 1024)\nx[0] = 1\nreturn len(x)",
            items=[], input_data={}, mode="all_items", timeout_seconds=60,
        )
        assert result.success is False
        assert "ran out of memory" in (result.error or "")
        assert "128 MB" in (result.error or "")

    @POSIX_ONLY
    def test_the_worker_still_works_right_after_a_runaway_script(self, monkeypatch):
        monkeypatch.setenv("CODE_NODE_MAX_MEMORY_MB", "128")
        ex = sce.SubprocessCodeExecutor()
        ex.run("python", "x = bytearray(400*1024*1024)\nx[0]=1\nreturn len(x)",
               [], {}, "all_items", 60)
        after = ex.run("python", "return [i*2 for i in range(5)]", [], {}, "all_items", 30)
        assert after.success is True
        assert after.result == [0, 2, 4, 6, 8]
