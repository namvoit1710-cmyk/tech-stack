"""One Claude call, with token rotation — extracted from the claude-worker's engine.

The claude-worker's ``ExecuteTaskUseCase._call_claude_cli`` is the piece every non-agentic
Claude node in this fleet actually uses (``classify_message``, ``summarize_log``,
``extract_reqid``). It lives inside a 1600-line use-case that also clones repos, opens
pull requests and drives a browser — none of which the cs-worker does, and all of which
would cost this image Node's git tooling and a 400 MB headless Chromium.

So the call is lifted out standalone, and only the paths this worker uses come with it:
**non-agentic, non-streaming, prompt on STDIN, one JSON object back**. The token-rotation
behaviour is a faithful port, including the sticky index and the two marker lists — those
are the parts that were learned from production, and a re-derivation would be a
regression waiting to happen.

Auth is the CLI's own ``CLAUDE_CODE_OAUTH_TOKEN``. ``ANTHROPIC_API_KEY`` is dropped for
the child so exactly one credential authenticates it. No API key is ever used
(``EGRESS_BYPASS`` — the CLI's own session is the sanctioned route).
"""

from __future__ import annotations

import json
import os
import threading
import subprocess
from typing import Any, Optional

# How many Claude CLI calls may run at once, across every node type this worker
# serves. Separate from WORKER_MAX_CONCURRENT on purpose: that budget also covers
# the log fetches, which are plain HTTPS and cost the token pool nothing. Default
# 3 because that is what has been running — not the token count, which is 1 and
# would serialise every summary.
_MAX_CONCURRENT = max(1, int(os.getenv("CLAUDE_MAX_CONCURRENT", "3") or 3))
_gate = threading.Semaphore(_MAX_CONCURRENT)

# Sticky start index across tasks, so a token found rate-limited is not re-hit first on
# the next task. A list (not an int) so the loop below can mutate it in place.
_TOKEN_IDX: list[int] = [0]

# Ported verbatim from the claude-worker engine. Matched ONLY against the CLI's own
# error channel (stdout+stderr+result), never against model output — see _invoke_once.
_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "usage limit", "rate limit", "rate_limit", "429", "too many requests",
    "overloaded", "capacity", "quota", "credit balance", "insufficient credit",
    "hit your", "session limit", "weekly limit", "monthly limit", "daily limit",
    "limit reached", "limit resets", "limit will reset", "resets ",
)
_AUTH_FAIL_MARKERS: tuple[str, ...] = (
    "authentication", "unauthorized", "401", "oauth", "token expired",
    "expired token", "please run /login", "not logged in", "login expired",
    "invalid token", "revoked",
)


def as_text(value: Any) -> str:
    """Coerce any resolved workflow input to text. An expression can resolve to a dict or
    a list (a whole node output bound to one field); stringify those to JSON so a later
    ``.strip()`` never hits ``'dict' has no attribute 'strip'``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _parse_cli_json(stdout: str) -> Optional[dict[str, Any]]:
    """Parse the CLI's JSON. ``--output-format json`` is one object; tolerate leading
    warning lines by falling back to the last line that parses to a ``result`` object."""
    if not stdout:
        return None
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def _looks_rate_limited(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in _RATE_LIMIT_MARKERS)


def _looks_auth_failure(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in _AUTH_FAIL_MARKERS)


def _invoke_once(cmd: list[str], prompt: str, env: dict[str, str],
                 timeout_secs: int) -> tuple[Optional[dict], int, str, str]:
    """One CLI invocation -> (parsed, returncode, combined_signal, stderr)."""
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_secs, env=env,
    )
    stdout = (proc.stdout or "").strip()
    stderr = proc.stderr or ""
    data = _parse_cli_json(stdout)
    combined = f"{stdout}\n{stderr}\n" + (as_text(data.get("result")) if data else "")
    return data, proc.returncode, combined, stderr


def call_claude(prompt: str, tokens: list[str], model: str = "", claude_bin: str = "claude",
                timeout_secs: int = 90) -> str:
    """Run one non-agentic ``claude -p`` and return its ``result`` text.

    Rotates across *tokens* on a rate-limit (any exit code) or an auth failure (non-zero
    exit only); a spawn error or timeout surfaces as-is, because retrying those on another
    token just multiplies the wait. Raises on failure — every caller catches and turns it
    into a graceful node payload, so a Claude outage degrades the answer rather than
    killing the run.
    """
    # Serialise at the CLI, not at the lease loop: a worker slot held here is a
    # summary waiting its turn, which is what we want; a worker slot NOT taken is a
    # log fetch that never started, which is not.
    with _gate:
        return _call_claude_locked(prompt, tokens, model, claude_bin, timeout_secs)


def _call_claude_locked(prompt: str, tokens: list[str], model: str,
                        claude_bin: str, timeout_secs: int) -> str:
    """``call_claude``'s body, with the concurrency gate already held."""
    args = [claude_bin, "-p", "--output-format", "json"]
    if model:
        args += ["--model", model]
    cmd = ["cmd", "/C", *args] if os.name == "nt" else args

    candidates: list[Optional[str]] = list(tokens) if tokens else [None]
    n = len(candidates)
    start = _TOKEN_IDX[0] % n

    for offset in range(n):
        idx = (start + offset) % n
        tok = candidates[idx]
        env = os.environ.copy()
        if tok:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
            env.pop("ANTHROPIC_API_KEY", None)

        try:
            data, rc, combined, stderr = _invoke_once(cmd, prompt, env, timeout_secs)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"claude CLI not found ('{claude_bin}'); install Claude Code or set 'claude_bin'."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude CLI timed out after {timeout_secs}s") from exc

        if data is not None and not data.get("is_error"):
            _TOKEN_IDX[0] = idx           # stick with what worked
            return as_text(data.get("result"))

        unavailable = _looks_rate_limited(combined) or (rc != 0 and _looks_auth_failure(combined))
        if unavailable and offset + 1 < n:
            _TOKEN_IDX[0] = (idx + 1) % n
            continue
        if unavailable:
            raise RuntimeError(
                f"all {n} Claude token(s) unavailable (usage/rate/session limit or auth) "
                f"- refresh/rotate and re-run.")
        if data is not None and data.get("is_error"):
            raise RuntimeError(f"claude CLI error: {data.get('result') or data.get('subtype')}")
        raise RuntimeError(
            f"claude CLI produced no parseable JSON (exit {rc}). stderr: {stderr[:600]}")

    raise RuntimeError(f"all {n} Claude token(s) unavailable")


def parse_json_object(raw: str) -> dict[str, Any]:
    """The JSON object out of a model reply, tolerating prose wrapped around it.
    ``{}`` when there is none — callers must have a non-model fallback for that."""
    text = (raw or "").strip()
    if "{" in text and "}" in text:
        text = text[text.index("{"): text.rindex("}") + 1]
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}
