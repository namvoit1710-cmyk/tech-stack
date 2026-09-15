"""WorkerRef — a reference to an execution worker.

The node catalog (the v1 palette and the v2 ``/node-catalog``) exposes a worker
as the *catalog id* ``worker:<type>`` (e.g. ``worker:http_request``); the
worker-execution service keys on the bare ``worker_type`` (``http_request``).
This value object owns that single convention so the ``worker:`` prefix is
defined in exactly one place instead of being re-derived ad hoc across providers
and use cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class WorkerRef:
    PREFIX: ClassVar[str] = "worker:"
    worker_type: str  # the bare type the executor understands

    @classmethod
    def parse(cls, raw: str | None) -> "WorkerRef | None":
        """Normalize a worker reference to a ``WorkerRef``.

        Lenient: accepts either the catalog id (``worker:http_request``) or the
        bare type (``http_request``) and always yields the bare type. Returns
        ``None`` for an empty/None reference.
        """
        if not raw:
            return None
        return cls(worker_type=raw.removeprefix(cls.PREFIX))

    @classmethod
    def from_catalog_id(cls, node_id: str) -> "WorkerRef | None":
        """Parse a catalog id; returns ``None`` if it is not a ``worker:`` ref.

        Strict counterpart to :meth:`parse` — used by the catalog dispatch to
        reject ``builtin:``/``business:``/bare ids.
        """
        if not node_id or not node_id.startswith(cls.PREFIX):
            return None
        worker_type = node_id.removeprefix(cls.PREFIX)
        return cls(worker_type=worker_type) if worker_type else None

    @property
    def catalog_id(self) -> str:
        """The catalog-id form (``worker:<type>``)."""
        return f"{self.PREFIX}{self.worker_type}"
