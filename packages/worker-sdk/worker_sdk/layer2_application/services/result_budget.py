"""Measure a worker result before it is handed to the executor.

WHY THIS EXISTS (tenant-1, run edda4f82-4a09-4587-beb5-8ed615ab8219,
2026-08-18): an http_request node returned a 12 980 452-byte result. The
executor could not publish it past the 1 048 576-byte Event Mesh cap, so the
node failed ``RESULT_TOO_LARGE`` and the run failed. That guard is correct and
it is the last line of defence — but it fires on the far side of the wire, and
the message it produces says only how many bytes there were, then points at
worker-side streaming (SA-1944) as the remedy. For that payload streaming would
have moved 204 bytes of 12 980 452: the oversized field was ``resolved_body``,
a ``str``, and ``should_stream`` only converts ``list``/``dict``.

So the number alone sends a reader the wrong way. What is needed is
attribution — WHICH field, HOW many bytes, and whether streaming could ever
have moved it — produced where the result is still in hand.

The input side already has a budget (``RESOLVE_MAX_INPUT_BYTES``). The output
side had none at all. This is that missing half.
"""

from __future__ import annotations

import json
from typing import Any, Optional

FILE_REF_MARKER = "__file_ref"

# How many fields the message names. The point is to finger the culprit, not to
# inventory the dict — and the message has a hard length budget below.
_FIELD_LIMIT = 4

# The executor truncates a worker error at 500 chars
# (``handle_worker_callback_usecase``: ``str(reason)[:500]``). A diagnostic that
# gets cut off is useless exactly when it matters, so it is built to fit.
_MESSAGE_LIMIT = 500


def _kind(value: Any) -> str:
    """The label used in the message: a file_ref is called what it is."""
    if isinstance(value, dict) and value.get(FILE_REF_MARKER):
        return "file_ref"
    return type(value).__name__


def _streamable(value: Any, scalars_streamable: bool = False) -> bool:
    """Whether worker-side streaming could have replaced this value.

    Mirrors ``StreamingOutputConverter.should_stream``: list/dict always, plus a
    string once scalar streaming is switched on. A value that is ALREADY a
    file_ref is a pointer, not something to convert.

    *scalars_streamable* is an ARGUMENT rather than a settings read because this
    module is layer 2 and the flag lives in layer 4 — the import-linter contract
    forbids the import, and threading it through keeps the mirror honest instead
    of hard-coding an answer that goes stale the day the flag flips.
    """
    if isinstance(value, dict) and value.get(FILE_REF_MARKER):
        return False
    if isinstance(value, str):
        return scalars_streamable and bool(value)
    return isinstance(value, (list, dict)) and bool(value)


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def measure_result(outputs: Any, scalars_streamable: bool = False) -> tuple[int, list[dict]]:
    """Return ``(total_bytes, per-field records sorted biggest first)``.

    Records are ``{field, bytes, kind, streamable}``. A non-dict result is
    reported as the single field ``<root>`` rather than refused — the caller
    still needs a number.
    """
    if not isinstance(outputs, dict):
        total = _size(outputs)
        return total, [{
            "field": "<root>",
            "bytes": total,
            "kind": _kind(outputs),
            "streamable": _streamable(outputs, scalars_streamable),
        }]

    fields = [
        {
            "field": name,
            "bytes": _size(value),
            "kind": _kind(value),
            "streamable": _streamable(value, scalars_streamable),
        }
        for name, value in outputs.items()
    ]
    fields.sort(key=lambda f: f["bytes"], reverse=True)
    return _size(outputs), fields


def budget_verdict(
    outputs: Any, budget_bytes: int, scalars_streamable: bool = False,
) -> Optional[str]:
    """``None`` when the result fits, else a one-line diagnostic naming names.

    Never raises. A result that cannot be measured returns ``None``: not being
    able to weigh something is not evidence that it is too heavy, and failing a
    task on that guess would be worse than the bug this guards.
    """
    if not outputs or budget_bytes <= 0:
        return None
    try:
        total, fields = measure_result(outputs, scalars_streamable)
    except Exception:  # pragma: no cover - defensive; _size already uses default=str
        return None
    if total <= budget_bytes:
        return None

    parts = []
    for f in fields[:_FIELD_LIMIT]:
        part = f"{f['field']}={f['bytes']} B {f['kind']}"
        if not f["streamable"]:
            part += " NOT-STREAMABLE"
        parts.append(part)

    msg = (
        f"worker result {total} B over the {budget_bytes} B budget "
        f"(RESULT_MAX_OUTPUT_BYTES); largest: " + ", ".join(parts)
    )
    if len(msg) > _MESSAGE_LIMIT:
        msg = msg[: _MESSAGE_LIMIT - 1] + "…"
    return msg
