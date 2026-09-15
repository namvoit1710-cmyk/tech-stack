import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from collections.abc import Callable


@dataclass(kw_only=True)
class DataReference:
    uri: str = ""
    content_type: str = ""
    size_bytes: int = 0
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# File-backed reference helpers
# ---------------------------------------------------------------------------

FILE_REF_MARKER = "__file_ref"
ROOT_ARTIFACT_ID = "__root__"


def is_file_ref(value: Any) -> bool:
    """Return True if *value* is a file-backed reference dict."""
    return isinstance(value, dict) and value.get(FILE_REF_MARKER) is True


# Worker types whose node output is kept INLINE instead of being converted to a
# file-backed CSV reference. A code node returns small, structured JSON that
# downstream nodes consume directly as real Python values; file-backing it would
# JSON-encode/stringify scalars, split arrays-of-objects into separate artifact
# CSVs, and surface opaque ``file_id`` pointers downstream instead of the
# original data. Keeping the raw dict end-to-end (run context, event write-set,
# variable resolution, and the node's own Output panel) preserves the values
# verbatim. Scoped by worker_type so no other node kind is affected.
# ``approval_flow`` emits a ``{nodes, edges}`` graph — structured JSON like a
# code node, not a CSV table; file-backing it would shred the graph into
# artifact CSVs and break ``GET /schema`` (which surfaces the graph inline).
INLINE_OUTPUT_WORKER_TYPES = frozenset({"code", "approval_flow"})


def keeps_inline_output(worker_type: Any) -> bool:
    """Return True when a node's output should stay inline (not file-backed).

    The worker identity is carried in a ``<scheme>:<name>`` form at runtime
    (e.g. ``"worker:code"``, ``"builtin:compute"``); the static save-time value
    may be the bare name (``"code"``). Normalise by taking the segment after the
    last ``":"`` so both forms match.
    """
    if not isinstance(worker_type, str):
        return False
    name = worker_type.strip().lower().rsplit(":", 1)[-1]
    return name in INLINE_OUTPUT_WORKER_TYPES


# SA-1951 / SA-1949 — the size ceiling on an inline output.
#
# ``keeps_inline_output`` is a statement about the SHAPE of a worker's output
# (structured JSON, not a CSV table), and it was written on the assumption that
# such an output is *small* — see the comment above. Nothing enforced that. A
# ``code`` node returning 60k rows produced a multi-megabyte inline value, and
# the same unconditional exemption then broke BOTH ways:
#
#   * flag ON  — worker-side streaming replaced the big field with a nested
#     file_ref, the CP kept the envelope inline, and no resolver strategy owned
#     the resulting "plain dict carrying a file_ref" shape (SA-1949).
#   * flag OFF — the raw megabytes rode a NodeOutputProduced event straight into
#     an Event Mesh publish, which 413s at ~1 MB (SA-1951).
#
# So the dangerous state was never "flag on" or "flag off" — it was that
# ``keeps_inline`` ignored SIZE. Above this ceiling we file-back the output like
# any other worker: correctness beats verbatim-ness once the value is too big to
# ride an event. Well under the ~900 KB control-plane event cap
# (``EVENT_MESH_MAX_PAYLOAD_BYTES``) because ``plan_state_updates`` writes the
# value twice (primary key + node.name alias).
INLINE_OUTPUT_MAX_BYTES = int(os.getenv("INLINE_OUTPUT_MAX_BYTES", str(256 * 1024)))


def inline_output_size_bytes(value: Any) -> int:
    """Serialized size of *value*, as it would ride an event.

    ``default=str`` mirrors the tolerance of the event serializer: a value that
    is not strictly JSON-serializable must still be *measured*, not crash the
    size check.
    """
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except Exception:
        # Unmeasurable ⇒ treat as too large. Failing closed keeps an exotic
        # value off the event bus rather than gambling that it fits.
        #
        # Broad on purpose, and (TypeError, ValueError) was too narrow: the
        # ``default=str`` this relies on calls ``repr``, so a value whose own
        # ``__repr__`` raises escaped the clause entirely and propagated out of
        # ``should_keep_inline`` — on the complete_task path, i.e. it failed the
        # run. Verified escaping before this change. The docstring already said
        # such a value "must still be MEASURED, not crash the size check"; the
        # clause simply did not deliver it.
        return INLINE_OUTPUT_MAX_BYTES + 1


def should_keep_inline(worker_type: Any, value: Any) -> bool:
    """True when *value* may stay inline: the worker type says so AND it fits.

    This is the predicate the control plane must use when deciding whether to
    skip ``store_data``. ``keeps_inline_output`` alone answers only half the
    question — see ``INLINE_OUTPUT_MAX_BYTES``.

    Callers MUST apply this in every place that mirrors the inline decision
    (``complete_task`` and ``OrchestrationEngine.apply_state_updates_as_files``),
    or the two paths diverge and the value gets re-file-backed behind one of them.
    """
    if not keeps_inline_output(worker_type):
        return False
    return inline_output_size_bytes(value) <= INLINE_OUTPUT_MAX_BYTES


def schema_hash(
    columns: list[str] | None,
    column_types: dict[str, str] | None,
) -> str:
    """Content hash of a descriptor's SCHEMA — the ``(columns, column_types)`` pair.

    A descriptor is not small. Measured over 3,680 real refs on tenant-1: p50
    4,977 B, p99 44,025 B, max 60,918 B, with up to 537 columns. But it barely
    VARIES — 2,702 refs collapse to **481 distinct** ``(columns, column_types)``
    pairs, 5.6x, taking 31.51 MB down to 7.908 MB. Strip columns/column_types/
    artifacts out of a descriptor and what is left is a flat ~214 B at every
    percentile.

    So the schema is stored once per distinct shape and refs point at it by this
    hash. Two canonicalisation rules carry that entire 5.6x, and each fails
    silently in a different direction if broken:

    * ``column_types`` is a MAPPING — key order is not part of the schema, so it
      is sorted. Skip this and the same schema hashes differently depending on
      dict insertion order, dedup quietly degrades toward 1x, and nothing
      anywhere reports a problem.
    * ``columns`` is a SEQUENCE — order IS the CSV header order, so it is left
      alone. Sort it "for consistency" and two genuinely different files share a
      hash, which is worse: the frontend renders one node's columns for another.

    ``hashlib``, never ``hash()`` — the builtin is salted per process, so a
    process restart would re-hash every schema and the dictionary would grow
    without bound while appearing to work.

    Returns ``""`` when there is no schema to describe (a value-mode CSV, an
    empty payload). Empty means "nothing to point at", not "the empty schema" —
    the join simply finds no row.

    sha256 hex is 64 chars, which is exactly ``AIW_SCHEMA_DEF.SCHEMA_HASH``'s
    ``NVARCHAR(64)``.
    """
    cols = list(columns or [])
    types = dict(column_types or {})
    if not cols and not types:
        return ""
    canonical = json.dumps(
        {"columns": cols, "column_types": types},
        sort_keys=True,          # sorts column_types' keys; leaves the list alone
        separators=(",", ":"),   # no incidental whitespace in the hashed bytes
        ensure_ascii=False,      # one encoding of a non-ASCII column name, not two
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def refresh_schema_hash(ref: dict[str, Any] | None) -> dict[str, Any] | None:
    """Recompute ``schema_hash`` from the dict's CURRENT columns/column_types.

    ``schema_hash`` is a pointer, not a fact: it is only meaningful while it
    equals ``schema_hash(ref["columns"], ref["column_types"])``. Anything that
    mutates either key invalidates it, and a stale pointer is worse than none —
    it resolves to a *different* schema, so the frontend renders one table's
    columns for another.

    This exists because ``add_artifact`` hashes at creation time while
    ``columns`` is stamped on afterwards by the recursive-split path
    (``data_store_service.py:1002``) and five other sites. Measured on live
    tenant-1 descriptors: of the artifacts that carry ``columns``, **13 had an
    EMPTY ``column_types``** — so the creation-time hash was ``""`` and their
    columns had no dictionary entry at all, which is precisely the silent gap
    ``AIW_SCHEMA_DEF`` exists to close.

    Call it after any write to ``columns`` or ``column_types``. Returns the same
    dict for chaining; ``None`` and non-dicts pass through untouched.
    """
    if not isinstance(ref, dict):
        return ref
    ref["schema_hash"] = schema_hash(ref.get("columns"), ref.get("column_types"))
    return ref


def build_file_ref(
    file_id: str,
    *,
    content_type: str = "text/csv",
    row_count: int = 0,
    columns: list[str] | None = None,
    column_types: dict[str, str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    version: int = 0,
    etag: str = "",
    artifact_id: str = ROOT_ARTIFACT_ID,
    value_kind: str = "",
    item_kind: str = "",
    size_bytes: int = 0,
    arrow_file_id: str = "",
    arrow_size_bytes: int = 0,
) -> dict[str, Any]:
    """Build a lightweight file reference dict for storage in run context.

    Extra fields introduced for the recursive node-data model:
      - ``version``: monotonic revision counter; bumped per successful edit.
      - ``etag``: content hash covering main_file_id + descendant file_ids + revision.
      - ``artifact_id``: stable dotted-path identifier (root uses ``__root__``).

    ``artifacts`` entries are themselves dicts that may carry their own
    ``artifacts`` list for arbitrary-depth nesting.

    SA-1905 — ``arrow_file_id`` / ``arrow_size_bytes`` appear only when a nested
    Arrow mirror was written. See the note at the bottom of this function.

    SA-1977 — ``value_kind`` / ``item_kind`` declare the ROOT TYPE of the value
    this ref points at (``array``/``object``/``string``/``number``/``boolean``/
    ``null``, plus the element kind for arrays). The writer has always known
    this — ``dict_to_csv`` and ``infer_column_types`` branch on
    ``isinstance(data, list)``, and ``_store_rows_recursive`` takes it as an
    ``as_list`` *parameter* — and then dropped it, leaving five readers to guess
    it back from the row count. Persisting it is what makes the guessing stop.
    Empty on refs created before this existed; readers fall back per-site (see
    ``value_kind.kind_of``), exactly as ``column_types`` already does. No
    migration.
    """
    ref = {
        FILE_REF_MARKER: True,
        "artifact_id": artifact_id,
        "file_id": file_id,
        "content_type": content_type,
        "row_count": row_count,
        "columns": columns or [],
        # Write-time {flat_column: type} schema so reads cast losslessly
        # instead of guessing. Empty for refs created before this existed.
        "column_types": column_types or {},
        # Content hash of the two keys above. Computed HERE rather than at the
        # call sites because there are 21 of them across 9 files, and a schema
        # dictionary that is only 95% populated dedupes nothing while looking
        # like it works. "" when there is no schema. See ``schema_hash``.
        "schema_hash": schema_hash(columns, column_types),
        "value_kind": value_kind or "",
        "item_kind": item_kind or "",
        # Byte size of the main CSV as written. ``_record_full_materialize`` and
        # the resolver's cache accounting already read this key — it was simply
        # never produced, so both fell back to "unknown" and SA-1942's size
        # guard had to spend a HEAD round-trip to learn what the writer knew.
        # 0 means "not declared" (every ref written before this), never "empty".
        "size_bytes": size_bytes,
        # SA-1905 — an Arrow IPC mirror of the SAME value, holding the nesting
        # natively instead of split across sibling CSVs joined on
        # ``__parent_row_id``. Reading it needs no download tree, no re-parse and
        # no join.
        #
        # A TOP-LEVEL KEY, deliberately NOT an entry in ``artifacts``. Five
        # consumers walk that list without filtering on type, and each would
        # break in its own way: ``is_lazy_range_eligible`` treats "has artifacts"
        # as "not row-rangeable"; ``probe_size_bytes`` counts every node with a
        # ``file_id`` and would inflate the OOM guard; ``build_descriptor_tree``
        # would give the FE a phantom table and then fire an OData query at a
        # BINARY file; ``register_artifacts`` would publish ``{{$artifacts.…}}``;
        # and ``data_edit_handler`` indexes ``artifacts[0]`` positionally.
        #
        # It is a CACHE POINTER AND ITS LOSS IS LEGAL. ``node_output_rows.flatten``
        # projects a fixed field set, so a ref rehydrated from the DB arrives
        # without it and reads CSV — correctly, just slower. That property is
        # what makes the whole feature revertable by clearing one env var.
        "artifacts": artifacts or [],
        "version": version,
        "etag": etag,
    }
    # Present ONLY when there is one. Emitting an empty pointer on every ref
    # would change the key set of every ref in the system for a feature that is
    # off — and `node_output_rows.flatten` projects a FIXED field set, so
    # `rebuild(flatten(ref))` would stop equalling `ref` for values that have
    # nothing to do with Arrow. Absent means "read the CSV", which is what a
    # rehydrated ref means anyway.
    if arrow_file_id:
        ref["arrow_file_id"] = arrow_file_id
        ref["arrow_size_bytes"] = arrow_size_bytes
    return ref


def drop_arrow_sidecar(file_ref: dict[str, Any]) -> None:
    """Invalidate the Arrow mirror after an edit anywhere in this ref's tree.

    A byte-level CSV edit swaps ``file_id`` in place — ``csv_document`` exists
    precisely so a steward can change one cell without rewriting the value — and
    the sidecar would then serve PRE-EDIT data. Silently, and only on the fast
    path, which is the worst possible shape for a bug: the slow path stays
    correct, so it would look intermittent.

    Dropping rather than regenerating is deliberate. Regenerating means reading
    the whole tree back and re-encoding it, which is exactly the cost the
    byte-level editor exists to avoid. Without the pointer the next read takes
    the CSV path — correct, just slower — and the next write puts a fresh one
    back. The pointer is a cache and its loss is always legal.

    Always the ROOT ref: one sidecar mirrors the whole tree, so an edit to any
    descendant invalidates it.
    """
    if not isinstance(file_ref, dict):
        return
    # Popped, not blanked: absent IS the encoding of "no sidecar", so a ref that
    # has been edited is byte-identical in shape to one that never had a mirror.
    file_ref.pop("arrow_file_id", None)
    file_ref.pop("arrow_size_bytes", None)


def add_artifact(
    file_ref: dict[str, Any],
    *,
    file_id: str,
    name: str = "",
    content_type: str = "text/csv",
    size_bytes: int = 0,
    artifact_type: str = "data",
    created_at: str = "",
    artifact_id: str = "",
    parent_row_key_column: str | None = None,
    column_types: dict[str, str] | None = None,
    columns: list[str] | None = None,
) -> dict[str, Any]:
    """Append an artifact to a file reference. Returns the artifact dict.

    Recursive-model additions:
      - ``artifact_id``: dotted path (e.g. ``"orders"`` or ``"orders.line_items"``).
        Defaults to ``name`` when omitted.
      - ``parent_row_key_column``: set to ``"__parent_row_id"`` on nested-array
        artifacts; ``None`` at the root level.
      - Each artifact dict carries its own empty ``artifacts: []`` list so the
        structure is recursive by construction.
    """
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()
    artifact = {
        "artifact_id": artifact_id or name,
        "file_id": file_id,
        "name": name,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "artifact_type": artifact_type,
        "parent_row_key_column": parent_row_key_column,
        "column_types": column_types or {},
        # One dictionary for both roots and artifacts — a child table's schema
        # repeats across iterations far more than a root's does. ``columns`` is
        # usually stamped on AFTER this returns, so callers that do so must
        # follow with ``refresh_schema_hash``; this value is only correct for an
        # artifact that never gains one.
        "schema_hash": schema_hash(columns, column_types),
        "created_at": created_at,
        "artifacts": [],
    }
    # Only when given. Most callers stamp ``columns`` on afterwards, and
    # emitting an empty list here would add a key those artifacts never had.
    if columns is not None:
        artifact["columns"] = list(columns)
    if "artifacts" not in file_ref:
        file_ref["artifacts"] = []
    file_ref["artifacts"].append(artifact)
    return artifact


def collect_file_ids(value: Any) -> set[str]:
    """Every ``file_id`` reachable from *value*, at any depth.

    Walks plain dicts and lists too, not just refs: a node output is an envelope
    whose fields may each hold a ref (``{"metadata": …, "result": <ref>}``), and
    a ref's ``artifacts`` nest recursively. Used to decide which of the files a
    worker reported are actually pointed at by the stored output — anything else
    is an orphan by construction, not by guess.

    Includes ``arrow_file_id``: a sidecar is still a file somebody has to pay
    for, and it is reachable exactly as long as its ref is.
    """
    found: set[str] = set()

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 64:      # a cycle cannot exist in JSON, but a bug can
            return
        if isinstance(node, dict):
            for key in ("file_id", "arrow_file_id"):
                fid = node.get(key)
                if isinstance(fid, str) and fid:
                    found.add(fid)
            for sub in node.values():
                walk(sub, depth + 1)
        elif isinstance(node, list):
            for sub in node:
                walk(sub, depth + 1)

    walk(value)
    return found


def get_artifacts(file_ref: Any) -> list[dict[str, Any]]:
    """Return the artifacts list from a file reference, or empty list."""
    if not is_file_ref(file_ref):
        return []
    return file_ref.get("artifacts", [])


def walk_file_ref(
    file_ref: Any,
    visitor: Callable[[dict[str, Any], int], None],
) -> None:
    """Depth-first traversal over a recursive file reference.

    Calls ``visitor(node, depth)`` for the root and every descendant artifact
    dict. Depth ``0`` is the root; each level of nesting increments by one.
    No-op if *file_ref* is not a file reference.
    """
    if not is_file_ref(file_ref):
        return

    def _walk(node: dict[str, Any], depth: int) -> None:
        visitor(node, depth)
        for child in node.get("artifacts", []) or []:
            _walk(child, depth + 1)

    _walk(file_ref, 0)


def find_artifact(
    file_ref: Any,
    artifact_id: str,
) -> Optional[dict[str, Any]]:
    """Return the artifact dict with matching ``artifact_id``, or ``None``.

    Matches against the root's ``artifact_id`` (``__root__`` by default) and
    every descendant's ``artifact_id``. First hit wins (ids are expected to be
    unique within the tree).
    """
    if not is_file_ref(file_ref):
        return None
    found: list[dict[str, Any]] = []

    def _visit(node: dict[str, Any], _depth: int) -> None:
        if found:
            return
        if node.get("artifact_id") == artifact_id:
            found.append(node)

    walk_file_ref(file_ref, _visit)
    return found[0] if found else None


def register_artifacts(
    variables: dict[str, Any],
    node_output: Any,
    *,
    node_id: str = "",
    task_id: str = "",
) -> None:
    """Register named artifacts from *node_output* into the global ``artifacts`` dict.

    The registry is run-level: ``run.context.variables["artifacts"][<name>]`` is a
    metadata object carrying provenance + file metadata. Downstream mappings
    reference it by name, e.g.::

        {{$artifacts.invoice_pdf}}          -> full metadata object
        {{$artifacts.invoice_pdf.file_id}}  -> opaque file handle
        {{$artifacts.invoice_pdf.filename}} -> display name

    *node_id* and *task_id* are recorded as provenance so consumers can trace
    which producing task a named artifact came from.

    Entries without a non-empty ``name`` or ``file_id`` are skipped (legacy
    behaviour preserved).
    """
    if not is_file_ref(node_output):
        return
    registered_at = datetime.now(timezone.utc).isoformat()
    for art in node_output.get("artifacts", []):
        name = art.get("name", "")
        fid = art.get("file_id", "")
        if not (name and fid):
            continue
        variables.setdefault("artifacts", {})[name] = {
            "file_id": fid,
            "filename": art.get("filename") or art.get("name") or None,
            "content_type": art.get("content_type", ""),
            "size_bytes": art.get("size_bytes", 0),
            "produced_by_node": node_id,
            "produced_by_task": task_id,
            "registered_at": registered_at,
        }
