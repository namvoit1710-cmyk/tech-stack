"""Isolated code executor: runs user scripts in a separate child process.

Isolation strategy
------------------
* **Separate process** — a crash or infinite loop in the user script cannot take
  down the worker.
* **Wall-clock timeout** — the parent kills the child (process group on POSIX)
  if it overruns.
* **Resource limits** — on POSIX the child caps its own CPU time, its address
  space (``RLIMIT_AS``), and blocks writing files to disk, before it runs any
  user code.
* **Output ceiling** — the child refuses to emit a result larger than
  ``CODE_NODE_MAX_OUTPUT_MB``.
* **Engine sandboxes** — Python builtins/imports are restricted in the runner;
  JavaScript runs in a V8 isolate with no fs/network.

Why the memory limits exist (measured 2026-07-14)
-------------------------------------------------
The worker and every runner child share **one** container memory quota (256 MB on
CF). Two things used to make that quota trivial to blow:

* the child had **no** memory limit at all — a script could allocate 700 MB inside
  a 256 MB container, and the kernel OOM-killer then took the *worker* down with it;
* the parent holds the child's entire stdout as a string and then builds the parsed
  object graph on top of it, so a result costs the parent roughly **5-6x** its
  serialized size.

The net effect: a few tens of MB of code-node output — one big node, or a handful of
ordinary ones running concurrently — SIGKILLed the worker, and Cloud Foundry restarted
the instance. Bounding the child's address space and its output size turns that
container kill into a clean, explainable node failure while the worker keeps serving.

On Windows (local dev only — production runs on Linux), resource limits and
process-group signalling are unavailable, so isolation degrades to the process
boundary plus the wall-clock timeout.
"""

import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
from typing import Any, Iterator

from app.layer2_application.interfaces.code_executor_interface import CodeExecutionResult
from app.layer4_frameworks.providers.runner.limits import (
    DEFAULT_MAX_CONCURRENT,
    ENV_MAX_CONCURRENT,
    describe_budget,
    limit_mb,
    memory_limit_message,
    resolve_limits,
)

_IS_POSIX = os.name == "posix"
_log = logging.getLogger(__name__)

# Admission control, shared by every code task in this worker process.
#
# The per-task ceilings bound ONE script; they do not bound the container, which is
# what actually kills the worker. The SDK runs sync use cases on a pool of
# MAX_WORKER_THREADS (8), so without this, eight individually-legal code nodes each
# hold their own child + parsed result out of one 256 MB quota. Measured: four
# concurrent nodes returning 20 MB each — all under the 32 MB output ceiling — still
# OOM-killed the worker. Bounding concurrency is what makes the per-task ceilings add
# up to something the container can actually hold.
_REQUESTED_CONCURRENT = limit_mb(ENV_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT)

# The ceilings are derived from the pod's real quota and the worker's real baseline, so
# they follow `memory:` and the concurrency instead of being a number someone typed. The
# concurrency comes back too: if the pod cannot afford the number asked for, we run fewer
# scripts rather than hand each one a ceiling that adds up to more than the container has.
_, _, _MAX_CONCURRENT = resolve_limits(_REQUESTED_CONCURRENT)
_SLOTS: threading.BoundedSemaphore | None = (
    threading.BoundedSemaphore(_MAX_CONCURRENT) if _REQUESTED_CONCURRENT > 0 else None
)

# An enforced limit nobody can see is a support ticket. Log the arithmetic once at boot.
_log.info(describe_budget(_REQUESTED_CONCURRENT))


@contextlib.contextmanager
def _slot() -> Iterator[None]:
    """Hold one of the worker's code-execution slots for the duration of a run.

    Blocking on purpose: the caller is already on a worker thread, and making a task
    wait a moment is the entire point — the alternative is the container dying and
    taking every in-flight task with it.
    """
    if _SLOTS is None:
        yield
        return
    _SLOTS.acquire()
    try:
        yield
    finally:
        _SLOTS.release()

# Root of the worker (the directory that contains the `app` package), so the
# child can import `app.layer4_frameworks.providers.runner`.
_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

# Grace added to the wall-clock timeout so the inner engine timeout fires first
# and returns a clean error before the parent force-kills the process.
_KILL_GRACE_SECONDS = 5


class SubprocessCodeExecutor:
    """ICodeExecutor implementation backed by an isolated runner subprocess."""

    def __init__(self, python_executable: str | None = None) -> None:
        self._python = python_executable or sys.executable

    def run(
        self,
        language: str,
        code: str,
        items: list[Any],
        input_data: dict[str, Any],
        mode: str,
        timeout_seconds: int,
    ) -> CodeExecutionResult:
        # One slot per concurrently-running script: the child's memory and the parsed
        # result both come out of the container quota this worker lives in.
        with _slot():
            return self._run(language, code, items, input_data, mode, timeout_seconds)

    def _run(
        self,
        language: str,
        code: str,
        items: list[Any],
        input_data: dict[str, Any],
        mode: str,
        timeout_seconds: int,
    ) -> CodeExecutionResult:
        max_memory_mb, max_output_mb, _ = resolve_limits(_REQUESTED_CONCURRENT)

        payload = json.dumps({
            "language": language,
            "code": code,
            "items": items,
            "input": input_data,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "max_memory_mb": max_memory_mb,
            "max_output_mb": max_output_mb,
        })

        try:
            proc = self._spawn()
        except Exception as exc:  # noqa: BLE001
            return CodeExecutionResult(
                success=False, error=f"Failed to start runner: {exc}"
            )

        try:
            stdout, stderr = proc.communicate(
                input=payload, timeout=timeout_seconds + _KILL_GRACE_SECONDS
            )
        except subprocess.TimeoutExpired:
            self._kill(proc)
            proc.communicate()
            return CodeExecutionResult(
                success=False,
                error=f"Script timed out after {timeout_seconds}s",
            )
        except BaseException:
            # Any other way out of communicate() — the calling thread being torn
            # down, MemoryError while buffering the child's output — must not leave
            # the child alive and holding the pipes.
            self._kill(proc)
            raise

        if proc.returncode != 0 and not stdout.strip():
            detail = self._describe_failure(
                proc.returncode, stderr, timeout_seconds, max_memory_mb
            )
            return CodeExecutionResult(success=False, error=detail)

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            detail = stderr.strip() or "runner returned malformed output"
            return CodeExecutionResult(success=False, error=detail)

        return CodeExecutionResult(
            success=bool(data.get("ok")),
            result=data.get("result"),
            logs=data.get("logs") or [],
            error=data.get("error"),
        )

    def _spawn(self) -> "subprocess.Popen[str]":
        """Start the runner child.

        ``start_new_session`` — **not** ``preexec_fn`` — puts the child in its own
        process group so the parent can kill the whole tree on timeout. This matters:
        ``exec_uc.execute`` is dispatched via ``asyncio.to_thread``, so ``Popen`` runs
        on a worker thread of a multi-threaded process, and CPython documents
        ``preexec_fn`` as unsafe there — the forked child can deadlock before ``exec``,
        and ``Popen()`` blocks until ``exec`` completes, so the calling thread would
        hang forever with no timeout to rescue it. ``start_new_session`` performs the
        same ``setsid()`` inside CPython's C helper, which is async-signal-safe. The
        resource limits are applied by the child itself after ``exec``, where it is
        single-threaded and Python code is safe to run.
        """
        env = dict(os.environ)
        env["PYTHONPATH"] = _WORKER_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        # The child sets RLIMIT_FSIZE=0, so a stray .pyc write would become a
        # spurious failure. Don't let it try.
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        return subprocess.Popen(
            [self._python, "-m", "app.layer4_frameworks.providers.runner"],
            cwd=_WORKER_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    @staticmethod
    def _describe_failure(
        returncode: int,
        stderr: str,
        timeout_seconds: int,
        max_memory_mb: int = 0,
    ) -> str:
        """Turn a non-zero/signal exit into a user-meaningful error.

        A negative ``returncode`` means the child was killed by signal
        ``-returncode`` (POSIX). The CPU-time guard (``RLIMIT_CPU``) fires SIGXCPU on
        a busy loop, and the timeout group-kill uses SIGKILL — both are effectively
        timeouts, so report them as such rather than leaking a raw "exited with code -N".

        A hard allocation failure can also kill the child before it can report
        anything itself; ``MemoryError`` on stderr is the tell.
        """
        if "MemoryError" in stderr:
            return memory_limit_message(max_memory_mb)

        if returncode < 0 and _IS_POSIX:
            import signal

            sig = -returncode
            if sig == getattr(signal, "SIGXCPU", 24):
                return f"Script exceeded the CPU time limit (~{timeout_seconds}s)"
            if sig in (getattr(signal, "SIGKILL", 9), getattr(signal, "SIGTERM", 15)):
                return f"Script timed out after {timeout_seconds}s"
            return stderr.strip() or f"Script terminated by signal {sig}"
        return stderr.strip() or f"runner exited with code {returncode}"

    @staticmethod
    def _kill(proc: "subprocess.Popen[str]") -> None:
        try:
            if _IS_POSIX:
                import signal

                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
