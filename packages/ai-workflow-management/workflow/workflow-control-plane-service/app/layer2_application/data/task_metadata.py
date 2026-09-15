"""What ``task.output_metadata`` IS — the contract, as code.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §18.

``AIW_TASKS.OUTPUT_METADATA`` has never had a definition, so it accumulated one
by accident: measured on tenant-1 on 2026-08-08, **99.997% of its 17.8 M chars
is the worker's own output**, echoed verbatim by a single assignment
(``complete_task_use_case.py`` ``task.output_metadata = req.output_metadata``),
while every key the control plane ever reads back totals **536 chars**.

This module is that definition. It is a module and not a docstring because
enumerations written in prose go stale here — the rule this codebase learned the
hard way is to count from a constant, a registry or a builder, never from a
sentence.

    output_metadata is TASK CONTROL STATE WRITTEN BY THE CONTROL PLANE.
    It is never the node's output.

Two populations, disjoint by construction because they are written by different
paths that never run for the same node kind:

1. **Dispatch contract** (:data:`DISPATCH_KEYS_BY_NODE_TYPE`) — written ONCE, at
   ``pending_base.py`` ``build_output_metadata``, when a PENDING node pauses the
   run. It is the *resolved* rendering contract handed to the frontend, and it
   is read back on every later ``GET /schema`` and on resume. It survives
   completion untouched: ``resume_pending_task_use_case.py`` sets
   ``TaskStatus.COMPLETED`` without assigning metadata.

2. **Terminal annotation** (:data:`TERMINAL_KEYS`) — written when a task ends in
   a way its status alone does not explain: an error code, a deliberately
   skipped branch leaf, the reason a task was cancelled.

Everything else is node OUTPUT and already has a home — proved per task in §17.8,
not assumed:

* file-backed workers → ``AIW_RUN_NODE_OUTPUT`` + the file service (96.3% of the
  payload bytes measured)
* ``code`` / ``approval_flow`` → ``AIW_EVENT_LOG`` ``state_updates``, because
  ``INLINE_OUTPUT_WORKER_TYPES`` keeps them inline on purpose and they therefore
  never become a ``file_ref`` (the remaining 3.7%)

**The name stays.** ``output_metadata`` is wrong for both populations, but it is
also the wire contract — ``CompleteTaskRequest.output_metadata`` over REST, the
gRPC ``TaskResult``, and ``GET /tasks/{id}``. Renaming it would break every
worker to fix a word. The meaning gets defined instead.

**No hard cap.** ``NVARCHAR(5000)`` was proposed so oversize "fails loudly";
measured, 113 of 204 real PENDING nodes carry a legitimate contract over 5000
chars and the largest is 23,468, so the cap would fail loudly on an INPUT node
dispatching (§17.4). The budget here is checked in code and **drops, never
blocks** — a fat metadata is a cost, a node that cannot dispatch is an outage.
"""
from __future__ import annotations

import json
import os
from typing import Any

# ---------------------------------------------------------------------------
# 1. The dispatch contract, per PENDING node type
# ---------------------------------------------------------------------------
# Transcribed from the seven ``build_output_metadata`` implementations. Kept
# honest by ``test_task_metadata_contract.py``, which calls each handler and
# compares the keys it actually produces against this table — so a handler that
# grows a key without updating this file fails the build rather than silently
# widening the contract.

_COMMON = frozenset({"node_type", "input_schema", "instruction", "prefilled"})

_DATA_EDIT = _COMMON | {
    "timeout_minutes",
    "file_id",
    "source_file_id",
    "source_node_id",
    "source_node_name",
    "source_columns",
    "source_row_count",
    "artifact_key",
    "query_params",
    "readonly",
    "custom_data_update",
    "actions",
    "settings",
    # SA-1239. camelCase deliberately: it is passed straight to the FE schema
    # synthesiser, which speaks the frontend's casing.
    "selectionMode",
}

DISPATCH_KEYS_BY_NODE_TYPE: dict[str, frozenset[str]] = {
    "INPUT": _COMMON | {"timeout_minutes"},
    "HUMAN_ACTION": _COMMON | {"confirm_label", "reject_label"},
    "FILE_UPLOAD": _COMMON | {"artifact_key"},
    "WAIT": _COMMON | {"wait_mode", "fire_at"},
    "DATA_EDIT": _DATA_EDIT,
    "TEMPLATE_DATA_EDIT": _DATA_EDIT | {"template_id", "business_data_model_id"},
}

DISPATCH_KEYS: frozenset[str] = frozenset(
    k for keys in DISPATCH_KEYS_BY_NODE_TYPE.values() for k in keys
)

# ---------------------------------------------------------------------------
# 2. Terminal annotation
# ---------------------------------------------------------------------------
TERMINAL_KEYS: frozenset[str] = frozenset({
    # fail_task_use_case, orchestration_engine (handler/expression failures),
    # edit_node_data. Read by GET /nodes/{id}/output and /execution.
    "error_code",
    # A FAILED task that was routed down a failure port is NOT a run failure.
    # Read by WorkflowRun.decide_completion — losing it fails the run.
    "skipped",
    # Why a task ended. Closed value set below.
    "reason",
    "cancelled_by",
    "failed_by",
})

# Every value ``reason`` is ever assigned, from the eight sites that write it.
# ``parent_timeout`` is the load-bearing one: resume_pending_task recognises a
# swept child task by this exact string and strips it on rehydrate.
REASON_VALUES: frozenset[str] = frozenset({
    "failure_routed",
    "child_workflow_failed",
    "run_cancelled",
    "parent_cancelled",
    "parent_timeout",
    "replaced_by_rerun",
    "replayed",
    "user_cancelled",
})

ALLOWED_KEYS: frozenset[str] = DISPATCH_KEYS | TERMINAL_KEYS

# ---------------------------------------------------------------------------
# 3. Budget
# ---------------------------------------------------------------------------
# Sized from the measurement, not from a round number: the largest legitimate
# dispatch contract on tenant-1 reconstructs to 23,468 chars, and AIW_SCHEMA_DEF
# holds a 33,205-char schema. 64 KB leaves ~2x headroom over the worst real
# value while still being 10x below the 642,530-char payload echo that motivated
# this file. Env-overridable because a legitimate schema is user-authored and
# this must never be the reason a node fails to dispatch.
TASK_METADATA_MAX_BYTES = int(os.getenv("TASK_METADATA_MAX_BYTES", str(64 * 1024)))

# off    — write exactly as today; narrow() reports but changes nothing.
# shadow — same, and the caller logs what WOULD be dropped. This is how the
#          allowlist earns trust against real traffic before anything is
#          deleted: the 2026-08-08 sample is post-purge and holds zero PENDING
#          tasks, so it cannot by itself prove the dispatch half.
# on     — actually narrow.
TASK_METADATA_MODE = os.getenv("TASK_METADATA_MODE", "off")


class MetadataReport:
    """What :func:`narrow` would do, so callers can log before they enforce."""

    __slots__ = ("kept", "dropped_keys", "kept_bytes", "dropped_bytes")

    def __init__(
        self,
        kept: dict[str, Any],
        dropped_keys: list[str],
        kept_bytes: int,
        dropped_bytes: int,
    ) -> None:
        self.kept = kept
        self.dropped_keys = dropped_keys
        self.kept_bytes = kept_bytes
        self.dropped_bytes = dropped_bytes

    @property
    def changed(self) -> bool:
        return bool(self.dropped_keys)

    def summary(self) -> str:
        return (
            f"kept {len(self.kept)} keys / {self.kept_bytes}B, "
            f"dropped {len(self.dropped_keys)} keys / {self.dropped_bytes}B: "
            f"{sorted(self.dropped_keys)[:12]}"
        )


def _size(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001 — a value that cannot serialise is a
        return 0       # payload problem, not a reason to fail a dispatch


def narrow(metadata: Any) -> MetadataReport:
    """Split *metadata* into the contract and everything else.

    Pure and total: it never raises and never mutates its argument. A non-dict
    (a worker returning a list, say) narrows to ``{}`` rather than blowing up
    the completion path — declining to store the wrong thing beats storing it.
    """
    if not isinstance(metadata, dict):
        return MetadataReport({}, [], 0, _size(metadata))

    kept: dict[str, Any] = {}
    dropped: list[str] = []
    kept_bytes = dropped_bytes = 0
    for key, value in metadata.items():
        n = _size(value)
        if key in ALLOWED_KEYS:
            kept[key] = value
            kept_bytes += n
        else:
            dropped.append(key)
            dropped_bytes += n

    # The budget is a second, independent gate: a key can be in the contract and
    # still be absurd (a source_columns list from a 10,000-column CSV). Drop the
    # payload-shaped keys first and keep the small control keys, because losing
    # `skipped` fails a run while losing `source_columns` degrades a table.
    if kept_bytes > TASK_METADATA_MAX_BYTES:
        for key in sorted(kept, key=lambda k: -_size(kept[k])):
            if kept_bytes <= TASK_METADATA_MAX_BYTES:
                break
            if key in TERMINAL_KEYS:
                continue  # never sacrifice the keys that decide run outcome
            n = _size(kept.pop(key))
            dropped.append(key)
            kept_bytes -= n
            dropped_bytes += n

    return MetadataReport(kept, dropped, kept_bytes, dropped_bytes)


def enforcing() -> bool:
    """True when :data:`TASK_METADATA_MODE` says to apply the narrowing."""
    return TASK_METADATA_MODE.strip().lower() == "on"


def observing() -> bool:
    """True in ``shadow`` — measure and log, change nothing."""
    return TASK_METADATA_MODE.strip().lower() == "shadow"
