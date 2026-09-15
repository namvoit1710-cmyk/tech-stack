"""Local durable spool for failed Event Mesh publishes (SA-1471).

When a terminally-failed publish can't reach the ``publisher.dlq`` queue —
DLQ disabled, HTTP client gone, or the broker itself is down (exactly the
moment durability matters most) — the envelope is appended to a local JSONL
spool instead of being dropped. The pump replays the spool back into the
send buffer when the circuit closes (and once at startup for leftovers).

Deliberately boring: one append-only JSONL file, a threading lock, atomic
drain-by-rewrite. Not a distributed log — a parking lot that turns
"message lost" into "message parked locally until the broker heals".
Bounded by ``max_entries`` so a long outage can't fill the disk.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_SPOOL_FILENAME = "eventmesh-dlq-spool.jsonl"
_DEFAULT_MAX_ENTRIES = 50_000


class DlqSpool:
    """Thread-safe JSONL spool. Disabled when ``spool_dir`` is empty."""

    def __init__(self, spool_dir: str, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._dir = (spool_dir or "").strip()
        self._max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._path = os.path.join(self._dir, _SPOOL_FILENAME) if self._dir else ""
        self._depth = 0
        if self._dir:
            try:
                os.makedirs(self._dir, exist_ok=True)
                self._depth = self._count_lines()
            except Exception:
                logger.exception(
                    "DLQ spool dir %r unusable — spool DISABLED for this process",
                    self._dir,
                )
                self._dir = ""
                self._path = ""

    @property
    def enabled(self) -> bool:
        return bool(self._dir)

    def depth(self) -> int:
        """Current number of parked envelopes (cheap cached counter)."""
        with self._lock:
            return self._depth

    def _count_lines(self) -> int:
        if not self._path or not os.path.exists(self._path):
            return 0
        with open(self._path, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def append(self, envelope: dict[str, Any]) -> bool:
        """Persist one envelope. Returns True when parked, False when the
        spool is disabled, full, or the write failed (caller records a loss)."""
        if not self.enabled:
            return False
        line = json.dumps(envelope, separators=(",", ":"))
        with self._lock:
            if self._depth >= self._max_entries:
                logger.error(
                    "DLQ spool full (%d entries) — refusing append; message lost",
                    self._depth,
                )
                return False
            try:
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        pass  # best-effort durability; flush already happened
                self._depth += 1
                return True
            except Exception:
                logger.exception("DLQ spool append failed — message lost")
                return False

    def drain(self, max_entries: int = 500) -> list[dict[str, Any]]:
        """Pop up to ``max_entries`` envelopes (FIFO). The remainder is
        rewritten atomically (tmp file + replace); corrupt lines are
        skipped and logged, never returned and never retained."""
        if not self.enabled:
            return []
        with self._lock:
            if not os.path.exists(self._path):
                self._depth = 0
                return []
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
            except Exception:
                logger.exception("DLQ spool read failed — leaving file untouched")
                return []

            popped_raw, remainder = lines[:max_entries], lines[max_entries:]
            popped: list[dict[str, Any]] = []
            corrupt = 0
            for raw in popped_raw:
                try:
                    popped.append(json.loads(raw))
                except Exception:
                    corrupt += 1
            if corrupt:
                logger.warning("DLQ spool drain skipped %d corrupt line(s)", corrupt)

            tmp_path = self._path + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as fh:
                    for raw in remainder:
                        fh.write(raw + "\n")
                os.replace(tmp_path, self._path)
            except Exception:
                # Rewrite failed: keep the original file intact and give the
                # caller nothing — replaying AND retaining the same entries
                # would double-publish on the next drain.
                logger.exception("DLQ spool rewrite failed — drain aborted")
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                return []

            self._depth = len(remainder)
            return popped
