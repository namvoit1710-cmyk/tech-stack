"""SA-892 (N10): bounded in-memory ``conv_id -> set[run_id]`` index.

An in-process store (no external dependency), matching the orchestrator's other
in-memory collaborators. Bounded so a long-lived process cannot leak:

* an LRU cap on the number of tracked conversations, and
* a FIFO cap on run ids per conversation.

Eviction is best-effort memory hygiene only — a stop for an evicted conversation
simply falls back to the control-plane lookup. Ordered dicts act as ordered sets
so the caps evict the *oldest* entries deterministically.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from app.layer2_application.interfaces.conversation_run_index import (
    IConversationRunIndex,
)

DEFAULT_MAX_CONVERSATIONS = 2048
DEFAULT_MAX_RUNS_PER_CONVERSATION = 64


class InMemoryConversationRunIndex(IConversationRunIndex):
    def __init__(
        self,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
        max_runs_per_conversation: int = DEFAULT_MAX_RUNS_PER_CONVERSATION,
    ) -> None:
        self._max_conversations = max(1, max_conversations)
        self._max_runs = max(1, max_runs_per_conversation)
        # conv_id -> ordered set of run ids (dict-as-ordered-set: value unused).
        self._index: "OrderedDict[str, OrderedDict[str, None]]" = OrderedDict()
        # SA-892 L1: both record() (writer) and get_run_ids() (reader) mutate the
        # OrderedDict (move_to_end / popitem). Today both callers are async HTTP
        # handlers on one event loop, but guard with a lock — like the sibling
        # CancelledRunRegistry / CancellationRegistry — so a future off-loop caller
        # cannot trip "OrderedDict mutated during iteration" / corrupt the LRU.
        self._lock = threading.Lock()

    def record(self, conv_id: str, run_id: str) -> None:
        conv_id = (conv_id or "").strip()
        run_id = (run_id or "").strip()
        if not conv_id or not run_id:
            return

        with self._lock:
            runs = self._index.get(conv_id)
            if runs is None:
                runs = OrderedDict()
                self._index[conv_id] = runs
            self._index.move_to_end(conv_id)  # LRU touch

            if run_id in runs:
                runs.move_to_end(run_id)
            else:
                runs[run_id] = None

            while len(runs) > self._max_runs:
                runs.popitem(last=False)  # drop oldest run id
            while len(self._index) > self._max_conversations:
                self._index.popitem(last=False)  # evict least-recently-used conv

    def get_run_ids(self, conv_id: str) -> set[str]:
        conv_id = (conv_id or "").strip()
        if not conv_id:
            return set()
        with self._lock:
            runs = self._index.get(conv_id)
            if runs is None:
                return set()
            self._index.move_to_end(conv_id)  # LRU touch
            return set(runs.keys())
