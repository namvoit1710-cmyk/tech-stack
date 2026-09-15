"""Entry point for the isolated runner subprocess.

Invoked by ``SubprocessCodeExecutor`` as ``python -m
app.layer4_frameworks.providers.runner``. Reads a JSON job from stdin, executes
the user script for the requested language, and writes a JSON result to stdout.

Contract
--------
stdin  : {"language", "code", "items", "input", "mode", "timeout_seconds",
          "max_memory_mb", "max_output_mb"}
stdout : {"ok", "result", "logs", "error"}

The process caps *itself* — CPU, address space, no disk writes — before it runs any
user code. It does that here, after ``exec``, rather than in the parent's
``preexec_fn``: the worker dispatches use cases on a thread pool, and CPython
documents ``preexec_fn`` as unsafe in a threaded process. Here the child is
single-threaded, so running Python to apply the limits is safe.
"""

import json
import sys
from typing import Any

from app.layer4_frameworks.providers.runner.limits import (
    apply_child_limits,
    block_disk_writes,
    memory_limit_message,
    output_limit_message,
)


def _dispatch(job: dict[str, Any]) -> dict[str, Any]:
    language = (job.get("language") or "").strip().lower()
    code = job.get("code") or ""
    items = job.get("items") or []
    input_data = job.get("input") or {}
    mode = job.get("mode") or "all_items"
    timeout_seconds = int(job.get("timeout_seconds") or 30)

    if language == "python":
        from app.layer4_frameworks.providers.runner.python_runner import run_python

        return run_python(code, items, input_data, mode)

    if language == "javascript":
        from app.layer4_frameworks.providers.runner.js_runner import run_js

        return run_js(code, items, input_data, mode, timeout_ms=timeout_seconds * 1000)

    return {
        "ok": False,
        "result": None,
        "logs": [],
        "error": f"Unsupported language: '{language}'",
    }


def _measure(payload: dict[str, Any]) -> int:
    """Serialized size of ``payload`` in bytes, without ever holding it in memory.

    ``iterencode`` yields the JSON in small chunks; we count them and drop them. With
    the default ``ensure_ascii=True`` the output is ASCII, so characters == bytes.
    """
    total = 0
    for chunk in json.JSONEncoder().iterencode(payload):
        total += len(chunk)
    return total


def _emit(payload: dict[str, Any], max_output_bytes: int) -> None:
    """Write ``payload`` to stdout, refusing to emit more than ``max_output_bytes``.

    The parent buffers whatever we write here and then parses it, which costs it
    ~5-6x the serialized size in RAM — out of the same container quota the worker
    itself lives in. So the ceiling has to be enforced *before* the first byte goes
    out: once the parent starts reading, it is already committed to holding it all.
    """
    if max_output_bytes:
        size = _measure(payload)
        if size > max_output_bytes:
            max_output_mb = max_output_bytes // (1024 * 1024)
            payload = {
                "ok": False,
                "result": None,
                # The result is gone, but the user's own prints are how they debug —
                # keep the tail. Bounded, because the error document must stay small.
                "logs": (payload.get("logs") or [])[-50:],
                "error": output_limit_message(size, max_output_mb),
            }

    # Stream it out chunk by chunk — never materialize the whole string.
    for chunk in json.JSONEncoder().iterencode(payload):
        sys.stdout.write(chunk)


def main() -> None:
    # Before anything else: no writing files to disk. stdout is a pipe, unaffected.
    block_disk_writes()

    try:
        job = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        json.dump(
            {"ok": False, "result": None, "logs": [], "error": f"Invalid job payload: {exc}"},
            sys.stdout,
        )
        return

    timeout_seconds = int(job.get("timeout_seconds") or 30)
    max_memory_mb = int(job.get("max_memory_mb") or 0)
    max_output_bytes = int(job.get("max_output_mb") or 0) * 1024 * 1024

    # Cap CPU and address space now: the job payload has already been read (so a
    # large *input* is not charged against the script's budget), and no user code
    # has run yet.
    apply_child_limits(timeout_seconds, max_memory_mb)

    try:
        result = _dispatch(job)
    except MemoryError:
        # RLIMIT_AS bit. Without it, this allocation would have eaten the container
        # quota that the worker shares with us, and the kernel OOM-killer would have
        # taken the *worker* down instead of just this script.
        result = {
            "ok": False,
            "result": None,
            "logs": [],
            "error": memory_limit_message(max_memory_mb),
        }
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        result = {
            "ok": False,
            "result": None,
            "logs": [],
            "error": f"Runner crashed: {type(exc).__name__}: {exc}",
        }

    try:
        _emit(result, max_output_bytes)
    except MemoryError:
        json.dump(
            {
                "ok": False,
                "result": None,
                "logs": [],
                "error": memory_limit_message(max_memory_mb),
            },
            sys.stdout,
        )


if __name__ == "__main__":
    main()
