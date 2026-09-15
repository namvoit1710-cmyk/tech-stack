"""Resolving a descriptor's schema from ``AIW_SCHEMA_DEF`` instead of the wire.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §17.8 / §19.

A ``file_ref`` on the wire is **90.4% schema**. Measured on tenant-1 on
2026-08-08 across 3,975 refs in 471 ``NodeOutputProduced`` events:

    columns        4,823,808 chars   49.9%
    column_types   3,924,597 chars   40.6%
    schema_hash      260,172 chars    2.7%
    file_id          169,506 chars    1.8%   <- the actual pointer

``schema_hash`` is already in every ref, and ``AIW_SCHEMA_DEF`` already stores
the pair it names. So the schema can be *looked up* rather than *carried*:
8,748,405 of 12,050,541 ``state_updates`` chars, 72.6% of the whole write-set.

**Why this is a safety fix and not only a size one.** The largest event today is
347,789 bytes — 38.6% of the 900 KB ``EVENT_MESH_MAX_PAYLOAD_BYTES`` — and
**85.7% of it is schema**. Inline VALUES are bounded by
``INLINE_OUTPUT_MAX_BYTES`` (256 KB, added after SA-1951, where an oversized
event 413'd and the node hung forever). The schema is bounded by nothing: it
scales with the artifact tree × the user's column count × the id/name alias. A
hash is 64 chars whatever the CSV looks like, so this puts a ceiling where there
is none.

**The failure mode this must never reach production with.** A hash whose entry
is absent cannot be expanded, and the reader does not fail on a missing schema —
``CsvConverter._unflatten`` falls back to ``_try_parse_value`` and **guesses the
types**. ``"007"`` becomes ``7``. That is the SA-1977 defect returning, silently.
Hence the modes below, and hence :func:`verify`.

Modes (``SCHEMA_REF_MODE``), defaulting to ``off``:

* ``off``    — refs carry the schema inline. Today, byte for byte.
* ``shadow`` — refs still carry it, AND every lookup is checked against the
  dictionary. This is what earns the switch: divergence is reported on real
  traffic before anything is removed.
* ``on``     — refs carry ``schema_hash`` only; readers expand.

``on`` additionally requires the read path to expand **before** any consumer
reads ``columns``. Two consumers outside ``_unflatten`` do:
``file_backed_resolver.py:505`` treats a non-empty ``columns`` as "this ref is
not empty", and ``:849`` serves ``{{$node.__columns}}`` to user expressions.
Flipping ``on`` without those is a silent behaviour change, which is why
:func:`strip` is not called anywhere yet.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from app.layer1_domain.entities.data_reference import schema_hash

SCHEMA_REF_MODE = os.getenv("SCHEMA_REF_MODE", "off")


def enforcing() -> bool:
    return SCHEMA_REF_MODE.strip().lower() == "on"


def observing() -> bool:
    return SCHEMA_REF_MODE.strip().lower() == "shadow"


class SchemaDictionary:
    """``schema_hash`` → ``{"columns": [...], "column_types": {...}}``.

    The cache never invalidates, and that is a property of the data rather than
    a gamble: ``AIW_SCHEMA_DEF`` is keyed by a hash **of its own content**, so an
    entry cannot change meaning — a different schema is a different key. 56 rows
    on tenant-1 against 3,975 refs.

    ``fetch`` takes a list of digests and returns ``{digest: schema}``; misses
    are simply absent. Batch-shaped on purpose: the repo's own docstring warns
    that reading one at a time "would merely trade N HTTP calls for N SQL ones".
    """

    __slots__ = ("_fetch", "_cache", "_misses")

    def __init__(self, fetch: Callable[[list[str]], dict[str, dict]]) -> None:
        self._fetch = fetch
        self._cache: dict[str, dict] = {}
        # Remembered separately from hits: a miss is NOT cacheable the way a hit
        # is. The writer may store the entry moments later (the mirror runs
        # after the event is published), so caching "absent" forever would turn
        # a race into a permanent hole.
        self._misses: set[str] = set()

    def get(self, digest: str) -> dict | None:
        if not digest:
            return None
        hit = self._cache.get(digest)
        if hit is not None:
            return hit
        found = self.get_many([digest])
        return found.get(digest)

    def get_many(self, digests: list[str]) -> dict[str, dict]:
        wanted = {d for d in digests if d}
        out = {d: self._cache[d] for d in wanted if d in self._cache}
        missing = sorted(wanted - out.keys())
        if not missing:
            return out
        try:
            fetched = self._fetch(missing) or {}
        except Exception:  # noqa: BLE001 — a dictionary outage must degrade to
            return out     # "use the inline copy", never to a failed resolve
        for digest, schema in fetched.items():
            if isinstance(schema, dict):
                self._cache[digest] = schema
                out[digest] = schema
                self._misses.discard(digest)
        self._misses.update(d for d in missing if d not in fetched)
        return out

    @property
    def misses(self) -> set[str]:
        return set(self._misses)


def _walk(ref: Any):
    """Yield every ref in the artifact tree, root first."""
    if not isinstance(ref, dict):
        return
    yield ref
    for child in ref.get("artifacts") or []:
        yield from _walk(child)


def digests_in(ref: Any) -> list[str]:
    """Every ``schema_hash`` in the tree — one batched lookup per descriptor."""
    return [d for d in (r.get("schema_hash") or "" for r in _walk(ref)) if d]


def verify(ref: Any, dictionary: SchemaDictionary) -> list[str]:
    """Shadow check. Returns one line per problem; empty means the dictionary
    could reproduce this descriptor exactly.

    Three distinct failures, deliberately not collapsed — they need different
    fixes:

    * ``missing``  — the writer never stored it (the gap that left 51 of 88
      hashes absent before the mirror moved to the engine).
    * ``differs``  — stored, but not what this ref carries. A dedup bug.
    * ``not-content-addressed`` — the stored pair does not hash to the key it is
      filed under. Only possible if ``schema_hash``'s canonicalisation changed,
      which is exactly the silent 1x-dedup regression its docstring warns about.
    """
    problems: list[str] = []
    for node in _walk(ref):
        digest = node.get("schema_hash") or ""
        if not digest:
            continue
        entry = dictionary.get(digest)
        if entry is None:
            problems.append(f"missing:{digest[:12]}")
            continue
        cols = entry.get("columns") or []
        types = entry.get("column_types") or {}
        if cols != (node.get("columns") or []) or types != (node.get("column_types") or {}):
            problems.append(f"differs:{digest[:12]}")
        if schema_hash(cols, types) != digest:
            problems.append(f"not-content-addressed:{digest[:12]}")
    return problems


def expand(ref: Any, dictionary: SchemaDictionary) -> Any:
    """Fill ``columns``/``column_types`` from the dictionary, in place.

    A node that already carries them is left alone — during the transition both
    shapes are on the wire at once, and old events are never rewritten, so a
    reader must handle both **permanently**, not just until the flag flips.
    """
    if not isinstance(ref, dict):
        return ref
    dictionary.get_many(digests_in(ref))
    for node in _walk(ref):
        if node.get("columns") or node.get("column_types"):
            continue
        entry = dictionary.get(node.get("schema_hash") or "")
        if entry is None:
            continue
        node["columns"] = list(entry.get("columns") or [])
        node["column_types"] = dict(entry.get("column_types") or {})
    return ref


def strip(ref: Any) -> Any:
    """Remove the inline schema, keeping the hash. Returns a NEW tree.

    Not wired anywhere yet — ``on`` is gated on the read path expanding first
    (see the module docstring). It exists so the size claim is testable now.
    """
    if not isinstance(ref, dict):
        return ref
    out = {k: v for k, v in ref.items() if k not in ("columns", "column_types")}
    if not (ref.get("schema_hash") or ""):
        # No hash means nothing could restore the schema. Keep it inline —
        # declining to shrink beats shrinking into an unreadable ref.
        out = dict(ref)
    if ref.get("artifacts"):
        out["artifacts"] = [strip(a) for a in ref["artifacts"]]
    return out
