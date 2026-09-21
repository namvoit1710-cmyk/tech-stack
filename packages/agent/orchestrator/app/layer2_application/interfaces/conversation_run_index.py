"""SA-892 (N10): a ``conv_id -> set[run_id]`` index owned by the orchestrator.

The workflow control-plane has no concept of a *conversation* — runs are keyed
on ``run_id`` / ``correlation_id`` only, and ``GET /runs?conversation_id=``
silently drops the unknown query param and returns an unfiltered page. So that
lookup cannot reliably resolve every in-flight run for a conversation and may
miss sibling runs (SA-892 AC2).

The orchestrator, however, observes run ids as they flow back through the
agent-action / results path (``wf_info.run_id``) for a known conversation. This
index is the orchestrator-side source of truth for the stop fan-out: run ids are
recorded here as they are first seen, and ``StopConversationUseCase`` unions the
index with the (best-effort) control-plane lookup so no sibling run is missed.
"""

from __future__ import annotations

from typing import Protocol


class IConversationRunIndex(Protocol):
    def record(self, conv_id: str, run_id: str) -> None:
        """Record that ``run_id`` belongs to ``conv_id`` (idempotent).

        Empty / blank ``conv_id`` or ``run_id`` are ignored.
        """
        ...

    def get_run_ids(self, conv_id: str) -> set[str]:
        """Return every run id observed for ``conv_id`` (empty set if none)."""
        ...
