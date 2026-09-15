"""``NodeInputEtag`` — per-node weak ETag for the recursive ``__file_ref`` tree.

A content hash that bumps whenever any descendant artifact's ``file_id`` changes
or the schema version changes. It is the sole currency for ``If-Match``
concurrency on ``PATCH /data`` and ``If-None-Match`` short-circuits on
``GET /data``. The file service is content-addressed (every upload → new
``file_id``), so bumping a descendant's id always bumps the node's ETag; a
monotonic root ``version`` is folded in so no-op re-uploads still appear distinct.
Deterministic and order-agnostic: descendant ids are sorted before hashing.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.layer1_domain.entities.data_reference import walk_file_ref


class NodeInputEtag:
    """Deterministic ETag derivation for a node's ``__file_ref`` tree."""

    @staticmethod
    def of(file_ref: Any, schema_version: str = "") -> str:
        """Return ``"sha256:<hex>"`` for the given file_ref + schema_version.

        Collects every ``file_id`` in the tree (root + descendants), sorts the
        descendant ids (root kept separate as primary identity), and folds in the
        root's monotonic ``version`` so no-op swaps still produce a fresh ETag.
        Empty / non-file_ref input yields a stable sentinel so 304 semantics work
        on uninitialised nodes.
        """
        if not isinstance(file_ref, dict) or not file_ref.get("__file_ref"):
            return "sha256:" + hashlib.sha256(
                f"empty|{schema_version}".encode("utf-8")
            ).hexdigest()

        main_file_id = file_ref.get("file_id", "")
        version = file_ref.get("version", 0)

        descendant_ids: list[str] = []

        def _collect(node: dict[str, Any], depth: int) -> None:
            if depth == 0:
                return  # skip the root — carried separately
            fid = node.get("file_id", "")
            if fid:
                descendant_ids.append(fid)

        walk_file_ref(file_ref, _collect)
        descendant_ids.sort()

        payload = "|".join([
            main_file_id,
            ",".join(descendant_ids),
            str(schema_version),
            str(version),
        ])
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def as_weak_header(etag: str) -> str:
        """Format a computed ETag as a weak HTTP header value ``W/"<etag>"``."""
        return f'W/"{etag}"'

    @staticmethod
    def matches_if_match(header: str | None, etag: str) -> bool:
        """True when an incoming ``If-Match`` header matches the ETag.

        Accepts both weak (``W/"sha256:..."``) and strong (``"sha256:..."``) forms —
        weak is equivalent here (OData row ordering can vary without data changing).
        """
        if not header:
            return False
        cleaned = header.strip()
        if cleaned.startswith("W/"):
            cleaned = cleaned[2:]
        cleaned = cleaned.strip('"')
        return cleaned == etag
