"""Service for storing and retrieving node data as CSV files.

Converts Python dicts to CSV, uploads to the file service, and returns
lightweight file reference dicts.  The ``extracted`` cache on each
reference allows downstream variable resolution without downloading
the CSV in most cases.

Array-of-objects fields are automatically split into separate artifact
CSV files so they support OData row-level access and avoid CSV cell
size limits.

All I/O methods (``store_data``, ``materialize``, and the internal
``_store_*`` helpers) are ``async def`` and must be awaited — they
delegate to the async ``IFileServiceClient``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
from typing import Any, TYPE_CHECKING

# SA-1905 — native streaming JSONL->CSV producer (libs/json-csv-streamer).
# Optional: absent in environments where the wheel isn't built, so import
# softly and fall back to the in-RAM path when it (or the feature flag) is off.
try:  # pragma: no cover - import guard
    import json_csv_streamer as _STREAMER
except Exception:  # pragma: no cover
    _STREAMER = None

from app.layer1_domain.entities.data_reference import (
    refresh_schema_hash,
    FILE_REF_MARKER,
    add_artifact,
    build_file_ref,
    is_file_ref,
)
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    OBJECT,
    VALUE_COLUMN,
    declared_item_kind,
    declared_kind,
    empty_for_kind,
    item_kind_of_inline,
    kind_of,
    kind_of_inline,
    ref_uses_value_column,
)
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.value_objects.csv_document import CsvDocument

if TYPE_CHECKING:
    from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient

_logger = logging.getLogger(__name__)

# Divergence taxonomy for the shadow comparator. A CLOSED set — it is a metric
# label, and an open one is how a metrics backend falls over.
#
# Derived by measurement, not by guessing: 25 realistic node-output shapes run
# through both readers WITH ``column_types`` supplied (as the control plane
# supplies them). 22 agree. The three that do not collapse to two root causes,
# and both are CSV being unable to represent emptiness — Arrow is right in both:
#
#   {"s": ""}          -> CSV reads null          (SA-1988)
#   {"a":1},{"late":""} -> CSV reads null on both rows
#   {"o": {}}          -> CSV drops the key entirely
#
# ``"03230"`` and ``"42"`` diverge only when ``column_types`` is ABSENT, so they
# are not contributors on the read path the control plane actually uses. That is
# worth stating because the first pass of this measurement forgot to pass the
# types and over-counted them.
_DIV_EMPTY_STRING = "empty_string_vs_null"
_DIV_DROPPED_EMPTY = "dropped_empty_container"
_DIV_INT_FLOAT = "int_vs_float"
_DIV_STR_NUM = "string_vs_number"
_DIV_NULL_VALUE = "null_vs_value"
_DIV_MISSING_KEY = "missing_key"
_DIV_TYPE = "type_mismatch"
_DIV_VALUE = "value_mismatch"
_DIV_SHAPE = "length_mismatch"
_DIV_UNKNOWN = "unknown"

_DIV_MAX_DEPTH = 24


def _divergence_kind(arrow: Any, csv: Any, depth: int = 0) -> str:
    """Name the FIRST way *arrow* and *csv* differ, walking in a fixed order.

    One label per mismatch, not a set: a value can diverge several ways at once,
    and joining them would make the label cardinality unbounded. Over many reads
    the distribution is what carries the information, and the first difference in
    a deterministic walk is an unbiased sample of it.

    Never raises — it runs on a path that already decided to return the CSV
    value, and a classifier that throws would turn an observation into an
    incident.
    """
    try:
        if depth > _DIV_MAX_DEPTH:
            return _DIV_UNKNOWN

        if isinstance(arrow, dict) and isinstance(csv, dict):
            only_arrow = [k for k in arrow if k not in csv]
            only_csv = [k for k in csv if k not in arrow]
            for k in sorted(only_arrow):
                # An empty container the CSV could not carry at all is its own
                # cause: it is a KEY that vanished, not a value that changed.
                if arrow[k] in ({}, []):
                    return _DIV_DROPPED_EMPTY
            if only_arrow or only_csv:
                return _DIV_MISSING_KEY
            for k in sorted(arrow):
                if _canon(arrow[k]) != _canon(csv[k]):
                    return _divergence_kind(arrow[k], csv[k], depth + 1)
            return _DIV_UNKNOWN

        if isinstance(arrow, list) and isinstance(csv, list):
            if len(arrow) != len(csv):
                return _DIV_SHAPE
            for a, c in zip(arrow, csv):
                if _canon(a) != _canon(c):
                    return _divergence_kind(a, c, depth + 1)
            return _DIV_UNKNOWN

        # leaves
        if arrow == "" and csv is None:
            return _DIV_EMPTY_STRING
        if csv == "" and arrow is None:
            return _DIV_EMPTY_STRING
        if arrow is None or csv is None:
            return _DIV_NULL_VALUE
        if isinstance(arrow, bool) != isinstance(csv, bool):
            return _DIV_TYPE
        if isinstance(arrow, (int, float)) and isinstance(csv, (int, float)):
            return _DIV_INT_FLOAT if type(arrow) is not type(csv) else _DIV_VALUE
        if isinstance(arrow, str) != isinstance(csv, str):
            return _DIV_STR_NUM if isinstance(arrow, (str, int, float)) else _DIV_TYPE
        if type(arrow) is not type(csv):
            return _DIV_TYPE
        return _DIV_VALUE
    except Exception:  # pragma: no cover - classification must never break a read
        return _DIV_UNKNOWN


def _canon(x: Any) -> str:
    """Same comparator ``_shadow_compare`` uses — ``1 == 1.0`` in Python, so an
    equality test would walk straight past the divergence being classified."""
    try:
        return json.dumps(x, sort_keys=True, default=repr)
    except Exception:  # pragma: no cover
        return repr(x)


def _content_key(content: bytes) -> str:
    """Content-addressable idempotency key for a CSV upload.

    The file service dedupes uploads carrying the same ``X-Idempotency-Key``
    (it returns the existing ``file_id`` instead of storing a duplicate).
    Hashing the exact CSV bytes means byte-identical re-uploads — a node
    re-run, a re-executed test run, the same input fanned out to a child
    workflow — collapse to a single stored file instead of allocating a fresh
    ``file_id`` every time. Any real edit changes the bytes → new hash → new
    ``file_id``, so mutated/versioned data is never accidentally aliased.

    Safe for ``store_data`` specifically because it only ever *creates* files
    and returns refs; it has no orphan-GC bookkeeping that a returned-existing
    ``file_id`` could confuse (unlike the PATCH ``edit_node_data`` path).
    """
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


class MaterializeTooLargeError(RuntimeError):
    """Raised by ``materialize_guarded`` when a file-backed value's probed size
    exceeds the configured cap.

    This is the SA-1942 OOM guard refusing to pull the whole value into
    control-plane RAM (``download_file`` → all bytes → ``csv_to_dict`` blows up
    5.5–9.3×). A distinct type so callers (the variable resolver, the
    child-workflow relay) can catch it specifically and surface an actionable
    resolution error instead of letting the pod OOM.
    """

    def __init__(
        self,
        file_ref: Any,
        size_bytes: int,
        max_bytes: int,
        context: str = "",
    ) -> None:
        self.file_id = file_ref.get("file_id", "") if isinstance(file_ref, dict) else ""
        self.size_bytes = int(size_bytes)
        self.max_bytes = int(max_bytes)
        self.context = context
        where = f" while resolving {context!r}" if context else ""
        super().__init__(
            f"file-backed value (file_id={self.file_id!r}, ~{self.size_bytes} bytes) "
            f"exceeds the materialize cap ({self.max_bytes} bytes){where} — refusing "
            "to download it whole into control-plane RAM (SA-1942 OOM guard). Read "
            "it by row index/range, iterate it in a LOOP node, or pass it downstream "
            "as a pointer instead of evaluating the whole value inline."
        )


# Ceiling on concurrent file-service downloads when re-joining a recursive
# split. Bounded, not unbounded: a wide tree against one file-service instance
# raises per-call latency under fan-out (measured 0.52s serial -> 0.53-0.97s at
# 8 in flight), and it also caps the legacy-ref overshoot on the byte budget.
_PREFETCH_CONCURRENCY = 8


class _ByteBudget:
    """Running download-byte budget for the multi-file materialize paths (SA-1942).

    The main-CSV path can cap on a single ``len(csv_bytes)`` check, but a
    manifest (``loop_chunk`` results, ``nested_array`` recursive split) and the
    ``array_field`` re-attach download MANY files and concatenate them — a
    single-file check never fires while cumulative RAM climbs into the GBs.
    ``add()`` after each download raises as soon as the running total crosses the
    cap, so peak RAM is bounded to ``max_bytes`` + one chunk even when the HEAD
    pre-check couldn't measure the value up front. ``max_bytes<=0`` disables it.
    """

    __slots__ = ("max_bytes", "used", "file_ref", "context")

    def __init__(self, max_bytes: int, file_ref: Any, context: str = "") -> None:
        self.max_bytes = int(max_bytes or 0)
        self.used = 0
        self.file_ref = file_ref
        self.context = context

    def add(self, nbytes: int) -> None:
        if self.max_bytes <= 0:
            return
        self.used += int(nbytes or 0)
        if self.used > self.max_bytes:
            raise MaterializeTooLargeError(
                self.file_ref, self.used, self.max_bytes,
                context=self.context or "multi-file-materialize",
            )


# SA-1905 — "the Arrow sidecar was not used", distinct from "it was used and the
# value is None". A plain ``None`` cannot say that: ``None`` is a legitimate
# materialized value for a null scalar root.
_ARROW_MISS = object()

# ``ARROW_READ_MODE`` values.
_ARROW_OFF = "off"
_ARROW_SHADOW = "shadow"
_ARROW_ON = "on"

# The Arrow IPC media type. Load-bearing, not cosmetic: the file service runs
# polars canonicalisation + row-id injection whenever
# ``filename.endswith(".csv") or "csv" in mime_type``
# (``canonical_csv_processor._should_inject_row_id``), which would rewrite — and
# destroy — an Arrow IPC file. The name must not end in ``.csv`` either.
ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.file"


class DataStoreService:
    """Store/retrieve node data as CSV files via the file service."""

    # File-service synthetic columns: injected on upload (see ``_upload_csv``)
    # / by schema provisioning, never part of the logical application data
    # (PATCH rejects them as reserved). ``_extract_columns`` already omits them
    # from a ref's ``columns``; ``materialize`` strips them symmetrically so the
    # reconstructed data matches what was stored.
    _SYNTHETIC_COLUMNS = frozenset({"__row_id", "__parent_row_id"})


    def __init__(
        self,
        file_service_client: IFileServiceClient,
        csv_converter: CsvConverter,
        chunked_output_enabled: bool = False,
        chunking_threshold_bytes: int = 1024 * 1024,
        recursive_split: bool = False,
        arrow_read_mode: str = _ARROW_OFF,
        arrow_min_bytes: int | None = None,
        native_split: bool = False,
        orphan_gc_enabled: bool = True,
        arrow_write_enabled: bool = False,
        arrow_strict_parity: bool = True,
        arrow_codec: str = "",
        materialize_max_bytes: int = 0,
        monitor: Any = None,
    ) -> None:
        self._file_client = file_service_client
        self._csv = csv_converter
        # SA-2256 — gate G6 depends on ``full_materialize_total`` permanently.
        # Without it a resolver that silently falls back to a whole-file
        # download on deep non-index paths passes every test and OOMs in
        # production; the ticket calls this the most-skipped safeguard in this
        # kind of work. Optional so nothing that constructs this service
        # without a monitor changes behaviour.
        self._monitor = monitor
        # SA-1942 — OOM guard for the READ side. ``materialize_guarded`` (used by
        # the variable resolver's deep-path fallback, the child-workflow output
        # relay, and HITL resume) refuses to full-download a file-backed value
        # whose probed size exceeds this cap, raising ``MaterializeTooLargeError``
        # instead of OOMing the pod. 0 = disabled (legacy unbounded materialize),
        # which is the default so every existing caller / test is unchanged.
        self._materialize_max_bytes = int(materialize_max_bytes or 0)
        # SA-1905 — when enabled and a list payload is estimated to exceed the
        # threshold, convert it to CSV via the streaming native producer
        # instead of the in-RAM dict_to_csv. Defaults keep the legacy path.
        self._chunked_enabled = chunked_output_enabled
        self._chunking_threshold = chunking_threshold_bytes
        # SA-1910 — recursively split nested array-of-dicts (any depth) into
        # child artifacts stamped __parent_row_id, re-joined on materialize.
        # OFF keeps the legacy depth-1 split (nested arrays = JSON cells).
        self._recursive_split = recursive_split
        # SA-1905 — the Arrow sidecar. Three read modes, defaulting to the one
        # that changes nothing:
        #   off    — never look at ``arrow_file_id`` (the default)
        #   shadow — read BOTH, log any disagreement, return the CSV value
        #   on     — return the Arrow value, falling back to CSV on any failure
        # ``strict_parity`` makes the Arrow reader reproduce the CSV reading of
        # the shapes where the two legitimately differ, so turning the feature
        # on is a provable no-op and "reads got fast" is not shipped in the same
        # breath as "this value means something new".
        # SA-1910 — build the nested-array tree with the crate instead of
        # ``_store_rows_python``. Off by default and with a live fallback, per
        # the off -> shadow -> on -> delete discipline: the two writers produce
        # the same tree (proved by test_split_rust_vs_python.py) but not the
        # same BYTES, so the same value stored either way gets a different
        # file_id, and that is a thing to turn on deliberately.
        self._native_split = bool(native_split)
        self._orphan_gc_enabled = bool(orphan_gc_enabled)
        self._arrow_read_mode = str(arrow_read_mode or _ARROW_OFF).lower()
        # Its OWN size floor. It used to borrow CHUNKING_THRESHOLD_BYTES, and
        # that turned out to define an EMPTY window: the borrowed default is
        # 1 MB, and a worker output above ~1 MB cannot reach the control plane
        # at all — the Event Mesh publish fails the broker cap first
        # ("message ... is 2314343 bytes, over the 1048576-byte broker cap").
        # So nothing was ever big enough to earn a sidecar and small enough to
        # arrive. 0 means "every file-backed value", which is what a shadow
        # soak wants.
        self._arrow_min_bytes = int(
            arrow_min_bytes if arrow_min_bytes is not None else chunking_threshold_bytes
        )
        self._arrow_write_enabled = bool(arrow_write_enabled)
        self._arrow_strict_parity = bool(arrow_strict_parity)
        # Normalised HERE so the crate never sees whatever an operator typed
        # into an env var, and so that every "is it set?" test below is one
        # truthiness check rather than five spellings of "none".
        self._arrow_codec = str(arrow_codec or "").strip().lower()
        if self._arrow_codec in ("none", "off"):
            self._arrow_codec = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def store_data(self, key: str, data: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        """Convert *data* to CSV, upload, and return a file reference dict.

        Array-of-objects fields are split into separate artifact CSVs
        so they support OData row-level queries and avoid cell size limits.
        Scalar fields and small values stay in the main CSV.

        SA-1977 — every ref returned from here DECLARES its root kind, and the
        shapes that used to crash the writer (a list of scalars, a bare scalar)
        are stored for real via the reserved ``__value`` column.
        """
        if isinstance(data, (list, tuple)):
            rows = list(data)
            # SA-1910 — recursive nested-array split (gated). FIRST, exactly as
            # before: with both flags on, checking ``_should_stream`` ahead of this
            # would silently disable the split for every list over the chunking
            # threshold — nested arrays would collapse back into JSON cells and the
            # ``nested_array`` child artifacts (and with them OData row access and
            # the steward's nested table editor) would vanish, with no warning.
            # (The two features not composing is pre-existing; this Story must not
            # be what quietly picks the winner.)
            if self._recursive_split and rows and all(isinstance(x, dict) for x in rows):
                return await self._store_rows_recursive(key, rows, as_list=True)
            # SA-1905 — a large list streams to CSV through the native producer
            # instead of the in-RAM dict_to_csv. Before the ``__value`` path: a big
            # list of scalars needs the streamer just as much as a big list of
            # objects (the streamer writes ``__value`` itself).
            if self._should_stream(rows):
                return await self._store_list_streaming(key, rows)
            # SA-1977 A4 — a list holding any non-dict element. ``_flatten``
            # would call ``.items()`` on it and raise ``'int' object has no
            # attribute 'items'``, so a node returning [1,2,3] could not store
            # its output at all. It goes to the reserved ``__value`` column.
            if self._is_value_root(rows):
                return await self._store_values(key, rows, root_is_list=True)
            return await self._store_list(key, rows)

        if not isinstance(data, dict):
            # SA-1977 A4 — a bare scalar / null root. One row, one ``__value``
            # column. (``"hello"`` used to raise the ``str`` flavour of the same
            # AttributeError; the CP's callers gate on dict/list today, so this
            # is the store honouring the contract rather than a live path.)
            return await self._store_values(key, [data], root_is_list=False)

        if self._recursive_split:
            return await self._store_rows_recursive(key, [data], as_list=False)

        # Separate array-of-objects fields from scalar/object fields
        scalar_data: dict[str, Any] = {}
        array_fields: list[tuple[str, list]] = []

        for field_key, field_value in data.items():
            if self._is_array_of_dicts(field_value):
                array_fields.append((field_key, field_value))
            else:
                scalar_data[field_key] = field_value

        # If no array fields, use the simple single-CSV path (most common case)
        if not array_fields:
            return await self._store_dict(key, data)

        # Store scalar fields in main CSV
        ref = await self._store_dict(key, scalar_data) if scalar_data else build_file_ref(
            file_id="", row_count=0, columns=[], value_kind=OBJECT,
        )

        # Store each array field as a separate artifact CSV
        for field_key, field_value in array_fields:
            artifact_csv, _, row_count = self._csv.dict_to_csv(field_value)
            if not artifact_csv:
                continue
            artifact_filename = f"{key}-{field_key}.csv"
            artifact_file_id = await self._upload_csv(artifact_csv, artifact_filename)
            if artifact_file_id:
                add_artifact(
                    ref,
                    file_id=artifact_file_id,
                    name=field_key,
                    content_type="text/csv",
                    artifact_type="array_field",
                    size_bytes=len(artifact_csv),
                    column_types=self._csv.infer_column_types(field_value),
                )
                # Store row_count on the artifact for metadata
                for art in ref.get("artifacts", []):
                    if art.get("file_id") == artifact_file_id:
                        art["row_count"] = row_count
                        # An ``array_field`` artifact IS an array of objects, by
                        # construction (``_is_array_of_dicts`` gated it). Declare
                        # it so the tree is self-describing at every level, not
                        # just the root.
                        art["value_kind"] = ARRAY
                        art["item_kind"] = OBJECT
                        break

        return ref

    async def materialize(
        self, file_ref: dict[str, Any], max_bytes: int = 0, context: str = "",
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Download the CSV and reconstruct the original data.

        ``max_bytes`` (SA-1942, default 0 = off): a post-download backstop for
        the main-CSV path. When set and the downloaded bytes exceed it, raise
        ``MaterializeTooLargeError`` BEFORE the ``csv_to_dict`` / ``json.loads``
        parse (which blows up 5.5–9.3×) — so even when the file service can't
        report size via HEAD, the guard still caps the worst-case RAM to the
        raw bytes (1×), not the parsed structure. Prefer ``materialize_guarded``
        (HEAD pre-check avoids the download entirely); this param is the fallback
        when HEAD is unavailable. Every existing caller passes 0 → unchanged.
        """
        if not is_file_ref(file_ref):
            return file_ref

        self._record_full_materialize(file_ref, context)

        # SA-1905 — the Arrow sidecar carries this value's nesting natively, so
        # reading it needs no download tree, no re-parse and no
        # ``__parent_row_id`` join.
        #
        # It sits in FRONT of every CSV branch, not among them. It used to sit
        # after the ``loop_chunk`` and ``nested_array`` branches, both of which
        # return — so on a ref with artifacts, which is the ONE shape this
        # feature exists for, it was unreachable. A live shadow soak recorded
        # zero samples and looked exactly like zero mismatches.
        #
        # The attempt runs against a throwaway budget of its own (see
        # ``_try_materialize_arrow``): ``_ByteBudget.add`` has no rollback, so
        # charging an aborted attempt to the live budget would make the CSV
        # fallback raise where CSV alone would have passed.
        arrow_value = await self._try_materialize_arrow(file_ref, max_bytes, context)
        if arrow_value is not _ARROW_MISS and self._arrow_read_mode == _ARROW_ON:
            return arrow_value

        shadowing = (
            arrow_value is not _ARROW_MISS
            and self._arrow_read_mode == _ARROW_SHADOW
        )
        try:
            result = await self._materialize_csv(file_ref, max_bytes, context)
        except Exception:
            # A sidecar read that succeeded while the CSV side failed leaves a
            # `hit` with no verdict, and then `hit` no longer equals
            # shadow_match + mismatch. That identity is exactly what was used to
            # argue "167 reads, 84% of them mismatched" — it happened to hold at
            # the time, but nothing made it hold. Counting the skip makes it
            # true by construction instead of by luck.
            if shadowing:
                self._arrow_outcome("compare_skipped", "csv_failed")
            raise

        # In shadow the CSV value always wins; the sidecar is only ever asked
        # whether it agrees.
        if shadowing:
            self._shadow_compare(file_ref, arrow_value, result)
        return result

    async def _materialize_csv(
        self, file_ref: dict[str, Any], max_bytes: int = 0, context: str = "",
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Every CSV reading path, exactly as it was.

        Split out of ``materialize`` so the Arrow attempt can wrap ALL of the
        branches below instead of racing the two that return early. Nothing in
        here changed; ``_record_full_materialize`` stays with the caller so a
        read is still counted once.
        """
        # SA-1905 F2 — a loop-results manifest has no main CSV; its
        # ``loop_chunk`` artifacts ARE the data. Concatenate every chunk (in
        # order) back into the full results list. Bounded per chunk on read.
        loop_chunks = [
            a for a in file_ref.get("artifacts", [])
            if a.get("artifact_type") == "loop_chunk" and a.get("file_id")
        ]
        if loop_chunks:
            # SA-1942 — a loop-results manifest concatenates every chunk; cap the
            # cumulative download so a large manifest can't OOM even when the HEAD
            # pre-check couldn't measure it (loop_chunk artifacts carry no size).
            budget = _ByteBudget(max_bytes, file_ref, context or "loop-manifest")
            rows: list[Any] = []
            # Chunks are fetched together but consumed IN ORDER below — the
            # concatenation order is the results' order, so it must not depend on
            # which download finished first.
            for _a in loop_chunks:
                _declared = int(_a.get("size_bytes") or 0)
                if _declared:
                    budget.add(_declared)
            chunk_bytes_by_id = await self._download_many(
                [a["file_id"] for a in loop_chunks]
            )
            for art in loop_chunks:
                chunk_bytes = chunk_bytes_by_id.get(art["file_id"])
                if isinstance(chunk_bytes, BaseException):
                    raise chunk_bytes
                if not chunk_bytes:
                    continue
                if not int(art.get("size_bytes") or 0):
                    budget.add(len(chunk_bytes))
                chunk_data = self._strip_synthetic_columns(
                    self._csv.csv_to_dict(chunk_bytes, art.get("column_types"))
                )
                if isinstance(chunk_data, list):
                    rows.extend(chunk_data)
                elif chunk_data:
                    rows.append(chunk_data)
            return rows

        # SA-1910 — recursive nested-array reconstruction. A ref stored with the
        # recursive split carries ``nested_array`` artifacts; re-join them at
        # their dotted paths, then strip synthetic columns deeply.
        if any(a.get("artifact_type") == "nested_array" for a in file_ref.get("artifacts", [])):
            # SA-1942 — a shared budget across the whole recursive walk (main +
            # every descendant CSV) so a deep/wide split can't OOM the parse.
            budget = _ByteBudget(max_bytes, file_ref, context or "nested-array-manifest")
            reconstructed = await self._reconstruct_recursive(file_ref, budget)
            # Yield before the deep strip — it is a second full walk of the
            # whole graph and would otherwise run welded to the last parse.
            # See ``_reconstruct_recursive`` for why these explicit yields are
            # needed at all now.
            await asyncio.sleep(0)
            return self._strip_synthetic_deep(reconstructed)

        file_id = file_ref.get("file_id", "")
        if not file_id:
            # SA-1977 — an empty value has no file. Which empty it is, is a fact
            # only the ref can supply: ``[]`` and ``{}`` both stored ``b""`` and
            # both used to read back as ``{}``.
            return empty_for_kind(kind_of(file_ref))

        # SA-1942 — running budget across the main CSV + any array_field
        # artifacts re-attached below, so the parse blow-up is capped BEFORE it
        # happens even when the HEAD pre-check couldn't measure the value. The
        # caller's ``context`` (e.g. the resolution path) keeps the message
        # actionable on this HEAD-unavailable fallback.
        budget = _ByteBudget(max_bytes, file_ref, context or "post-download")
        csv_bytes = await self._file_client.download_file(file_id)
        if csv_bytes is None:
            _logger.warning("File %s not found, returning empty dict", file_id)
            return {}
        budget.add(len(csv_bytes))

        # HTTP-node responses (and other JSON payloads) are stored verbatim;
        # parsing them as CSV yields garbage, which breaks deep-path variable
        # resolution (e.g. {{$httpNode.metadata.response_body.<json.path>}}).
        # Detect JSON by content-type or a leading brace/bracket and parse it as
        # JSON; fall back to the CSV path on any failure so tabular files are
        # unaffected.
        content_type = str(file_ref.get("content_type") or "").lower()
        sniff = csv_bytes.lstrip()[:1]
        if "json" in content_type or sniff in (b"{", b"["):
            try:
                return json.loads(csv_bytes)
            except (ValueError, TypeError):
                pass

        result = self._strip_synthetic_columns(
            self._csv.csv_to_dict(
                csv_bytes,
                file_ref.get("column_types"),
                value_kind=kind_of(file_ref),
                item_kind=declared_item_kind(file_ref),
            )
        )

        # Re-attach array artifact fields to the materialized dict. Fetched all
        # at once for the same reason the recursive join is (one round-trip per
        # artifact, serially, for files whose ids the ref already names), and
        # charged the same way: ``store_data`` stamps ``size_bytes`` on every
        # ``array_field`` it writes, so a declared oversize is refused before
        # any download. Per-artifact error handling below is untouched — this
        # path deliberately warns and carries on where the recursive one raises.
        if isinstance(result, dict):
            array_arts = [
                a for a in file_ref.get("artifacts", [])
                if a.get("artifact_type") == "array_field" and a.get("file_id")
            ]
            for _a in array_arts:
                _declared = int(_a.get("size_bytes") or 0)
                if _declared:
                    budget.add(_declared)
            fetched = (
                await self._download_many([a["file_id"] for a in array_arts])
                if array_arts else {}
            )
            for art in array_arts:
                try:
                    art_bytes = fetched.get(art["file_id"])
                    if isinstance(art_bytes, BaseException):
                        raise art_bytes
                    if art_bytes:
                        if not int(art.get("size_bytes") or 0):
                            budget.add(len(art_bytes))  # SA-1942, undeclared
                        art_data = self._strip_synthetic_columns(
                            self._csv.csv_to_dict(
                                art_bytes, art.get("column_types"),
                                # An ``array_field`` artifact IS an array. The
                                # re-wrap below patched over the row-count
                                # guess; with the kind declared it never has
                                # to (and a 0-row child is [] instead of {}).
                                value_kind=declared_kind(art),
                                item_kind=declared_item_kind(art),
                            )
                        )
                        if not isinstance(art_data, list):
                            art_data = [art_data] if art_data else []
                        result[art["name"]] = art_data
                except MaterializeTooLargeError:
                    # SA-1942 — the OOM guard must propagate, not be masked as
                    # a per-artifact "failed to materialize" warning.
                    raise
                except Exception as exc:
                    _logger.warning(
                        "Failed to materialize artifact %s: %s", art["name"], exc,
                    )

        return result

    # ------------------------------------------------------------------
    # SA-1942 — size-guarded materialize (OOM safety on the read side)
    # ------------------------------------------------------------------

    async def probe_size_bytes(self, file_ref: Any) -> int | None:
        """Best-effort total byte size of a file-backed value WITHOUT downloading.

        Sums the main file's size plus EVERY descendant's — using each node's
        own declared ``size_bytes`` when the ref carries it (no probe), else a
        HEAD probe on that file. Returns ``None`` when nothing could be measured
        (not a file_ref, or the file service can't report size — e.g. no HEAD
        route), so the caller can fall back rather than block. Note ``row_count``
        is NOT used: a large JSON blob (HTTP-node response) is a single "row"
        but many megabytes, so bytes is the only reliable OOM signal.

        The walk is RECURSIVE. It used to stop after the top-level artifacts,
        which is exactly the shape the recursive split produces: measured on a
        41-file tree it reported 35% of the real bytes, so a 64 MB cap actually
        admitted ~180 MB — an OOM guard under-counting on the one shape it was
        written for. Probes for undeclared nodes are issued concurrently for the
        same reason the downloads are (a wide tree cost one serial HEAD per
        artifact); declared sizes cost nothing at all.
        """
        if not is_file_ref(file_ref):
            return None

        nodes: list[dict[str, Any]] = []

        def walk(node: dict[str, Any]) -> None:
            if node.get("file_id"):
                nodes.append(node)
            for art in node.get("artifacts") or []:
                walk(art)

        walk(file_ref)

        total = 0
        measured = False
        to_probe: list[str] = []
        for node in nodes:
            declared = int(node.get("size_bytes") or 0)
            if declared > 0:
                total += declared
                measured = True
            else:
                to_probe.append(node.get("file_id", ""))

        if to_probe:
            sem = asyncio.Semaphore(_PREFETCH_CONCURRENCY)

            async def _head(file_id: str) -> Any:
                async with sem:
                    try:
                        return await self._file_client.head_file(file_id)
                    except Exception:  # pragma: no cover - probe is best-effort
                        return None

            for head in await asyncio.gather(*(_head(f) for f in to_probe)):
                if head is not None and head.get("size_bytes") is not None:
                    total += int(head.get("size_bytes") or 0)
                    measured = True

        return total if measured else None

    # ------------------------------------------------------------------
    # SA-2256 — full-materialize accounting (gate G6)
    # ------------------------------------------------------------------

    @staticmethod
    def _full_materialize_cause(file_ref: dict[str, Any]) -> str:
        """Why this value could not be read lazily.

        The label is the whole point of the counter: after P2-1 makes the lazy
        row-range the default, ``flat_full_download`` should approach zero
        while the manifest causes remain — a single total would hide that.
        """
        artifacts = file_ref.get("artifacts") or []
        if any(a.get("artifact_type") == "loop_chunk" for a in artifacts):
            return "loop_manifest"
        if artifacts:
            return "artifacts_not_row_rangeable"
        return "flat_full_download"

    # Above this, a full materialize is worth waking someone for. Below it the
    # event is real but harmless — see the note at the log call.
    _FULL_MATERIALIZE_WARN_BYTES: int = 1024 * 1024

    # Size buckets for ``full_materialize_size_total``. Bounded on purpose:
    # 3 causes x 7 buckets = 21 series, and no run_id — the counter that already
    # exists says how OFTEN a whole-file read happens and nothing says how BIG,
    # which is why "is flat_full_download expensive?" could only be guessed.
    #
    # NOT an IMonitor histogram: ``PrometheusMonitor._get_histogram`` hard-codes
    # ``_DEFAULT_BUCKETS`` (0.001..5.0, request-latency seconds) with no way to
    # pass others, so every byte observation would land in ``+Inf``. That is the
    # same defect ``eventmesh_flush_*`` already carries; a label on a counter
    # avoids repeating it without touching the shared IMonitor contract.
    _SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
        (64 * 1024, "<64K"),
        (1024 * 1024, "64K-1M"),
        (8 * 1024 * 1024, "1M-8M"),
        (64 * 1024 * 1024, "8M-64M"),
        (256 * 1024 * 1024, "64M-256M"),
    )

    @staticmethod
    def _declared_tree_bytes(file_ref: dict[str, Any]) -> int:
        """Declared bytes for a ref INCLUDING every descendant. No I/O.

        The recorder used to read the ROOT's ``size_bytes`` alone. For
        ``artifacts_not_row_rangeable`` — a cause that by definition only fires
        on a tree — the root IS the envelope and every byte is in the children,
        so the size label was systematically wrong on exactly the cause it
        exists to describe. Measured live 2026-08-21, run ``d93b7a38``:

            root                size_bytes =         23
            child "duplicates"  size_bytes = 10 775 163   (30 000 rows)

        A 10.28 MiB materialize was published as ``size_bucket="<64K"``.

        ``probe_size_bytes`` already learned this on the guard side — "it used
        to stop after the top-level artifacts ... measured on a 41-file tree it
        reported 35% of the real bytes" — and its docstring also records why
        this version is free: "declared sizes cost nothing at all".

        Unlike ``probe_size_bytes`` this NEVER probes. A recorder that can block
        on the file service is a recorder that can stall the event loop, and on
        ``instances: 1`` that stalls the whole tenant. A node with no declared
        size contributes 0, so the total is a FLOOR — the honest direction to be
        wrong in, and ``0`` still reaches ``_size_bucket`` as "unknown".

        Iterative, not recursive: the shape being measured is attacker-adjacent
        (a ref comes back from a worker) and a deep tree must not be able to
        raise ``RecursionError`` inside a metrics path.
        """
        total = 0
        stack: list[Any] = [file_ref]
        seen = 0
        while stack and seen < 100_000:
            node = stack.pop()
            seen += 1
            if not isinstance(node, dict):
                continue
            try:
                total += int(node.get("size_bytes") or 0)
            except (TypeError, ValueError):
                pass
            children = node.get("artifacts")
            if isinstance(children, list):
                stack.extend(children)
        return total

    @classmethod
    def _size_bucket(cls, size: int) -> str:
        """Bucket a declared byte size, with 0 kept SEPARATE from "small".

        A ref written before ``size_bytes`` was stamped declares 0. Folding that
        into ``<64K`` would read as "this materialize was tiny" when the truth is
        "nobody knows" — the opposite of what this counter exists to answer.
        """
        if size <= 0:
            return "unknown"
        for limit, label in cls._SIZE_BUCKETS:
            if size < limit:
                return label
        return ">256M"

    def _record_full_materialize(self, file_ref: dict[str, Any], context: str) -> None:
        """Count and log one whole-file download into control-plane RAM.

        Every full materialize is observable, by design: a fallback nobody can
        see is indistinguishable from a fast path until the pod dies.
        """
        if not file_ref.get("file_id") and not file_ref.get("artifacts"):
            # An empty value has no file — nothing is downloaded, so counting
            # it would inflate the rate G6 is measured against.
            return

        cause = self._full_materialize_cause(file_ref)
        size = self._declared_tree_bytes(file_ref)

        run_id = ""
        try:
            # Local import: ``variable_resolver`` sits above this module in the
            # import graph, so a top-level import would close a cycle.
            from app.layer2_application.resolve.variable_resolver import (
                get_current_run_id,
            )
            run_id = get_current_run_id() or ""
        except Exception:  # pragma: no cover - contextvar unavailable
            run_id = ""

        # ``context`` is the resolution expression (e.g. "httpNode.body.items").
        # Its first segment IS the node reference; there is no ambient node-id
        # contextvar at this layer, so derive it rather than invent one.
        node = context.split(".")[0] if context else ""

        if self._monitor is not None:
            try:
                self._monitor.increment(
                    "full_materialize_total", tags={"cause": cause},
                )
            except Exception:  # pragma: no cover - metrics must never break IO
                _logger.debug("full_materialize_total increment failed", exc_info=True)
            try:
                # A SEPARATE metric, deliberately. ``full_materialize_total``
                # froze its label set as {cause} on first observation, and
                # ``PrometheusMonitor._get_counter`` returns None — dropping the
                # increment SILENTLY — when a later call passes different keys.
                # Adding size_bucket to the existing name would therefore break
                # the metric it is meant to enrich.
                #
                # ``size`` is the WHOLE tree's declared bytes, not the root's.
                # It was the root's, and the stated reason — "measuring them
                # here would mean I/O inside a recorder" — was simply wrong:
                # every child carries its own ``size_bytes`` in the ref already.
                # See ``_declared_tree_bytes`` for the live measurement that
                # showed a 10.28 MiB read published as ``<64K``.
                self._monitor.increment(
                    "full_materialize_size_total",
                    tags={"cause": cause, "size_bucket": self._size_bucket(size)},
                )
            except Exception:  # pragma: no cover - metrics must never break IO
                _logger.debug(
                    "full_materialize_size_total increment failed", exc_info=True,
                )

        # WARNING only when the size can actually hurt. Measured live 2026-08-21,
        # a real production tree materialize logged `size_bytes=2547` at WARNING —
        # and `artifacts_not_row_rangeable` fires ~65x per 30 days at that scale.
        # An alert that shouts for 2.5 KB is one nobody reads when a 54 MB value
        # arrives, and 54 MB is the case that took the pod to 100% of its cgroup
        # limit. The threshold is CHUNKING_THRESHOLD_BYTES, the same 1 MB that
        # decides tree-vs-flat, so the loud band is exactly the band that can be
        # large.
        #
        # size == 0 stays LOUD: a ref written before ``size_bytes`` was stamped
        # declares 0, and "nobody knows" must not be silenced as "it was small".
        _log_at = (
            _logger.warning
            if (size <= 0 or size >= self._FULL_MATERIALIZE_WARN_BYTES)
            else _logger.info
        )
        _log_at(
            "FULL MATERIALIZE (SA-2256) — downloading a whole file-backed value "
            "into control-plane RAM: cause=%s run_id=%s node=%s expression=%s "
            "file_id=%s size_bytes=%s",
            cause, run_id or "-", node or "-", context or "-",
            file_ref.get("file_id") or "-",
            size if size > 0 else "unknown",
        )

    async def materialize_guarded(
        self,
        file_ref: Any,
        *,
        max_bytes: int | None = None,
        context: str = "",
    ) -> Any:
        """``materialize`` with an OOM size cap (SA-1942).

        ``max_bytes`` defaults to the instance cap ``_materialize_max_bytes``
        (0 = disabled). When the guard is active and the value's probed size
        (:meth:`probe_size_bytes`) exceeds the cap, raise
        ``MaterializeTooLargeError`` — the whole value is never downloaded. When
        the size can't be probed (HEAD unavailable), fall through to
        ``materialize`` WITH the post-download byte backstop so the parse
        blow-up is still bounded. A non-file_ref, or ``max_bytes<=0``, is a
        transparent pass-through to ``materialize`` (zero added I/O), so
        guard-off callers and tests behave exactly as before.
        """
        cap = self._materialize_max_bytes if max_bytes is None else int(max_bytes)
        if not is_file_ref(file_ref) or cap <= 0:
            return await self.materialize(file_ref)

        size = await self.probe_size_bytes(file_ref)
        if size is not None and size > cap:
            raise MaterializeTooLargeError(file_ref, size, cap, context=context)
        # Size unknown (or under cap): materialize, but keep the post-download
        # backstop so an unprobeable oversize main CSV still can't OOM the parse.
        return await self.materialize(file_ref, max_bytes=cap, context=context)

    @staticmethod
    def is_file_ref(value: Any) -> bool:
        """Check whether *value* is a file-backed reference dict."""
        return is_file_ref(value)

    @staticmethod
    def is_lazy_range_eligible(value: Any) -> bool:
        """True when *value* is stored FLAT enough to be read by row-range,
        WITHOUT downloading the whole file (SA-1905 D2.4).

        Eligible = a file_ref with a ``file_id`` and NO artifacts. Any
        artifacts (``nested_array`` recursive rows, ``array_field`` object
        children, ``loop_chunk`` result manifests) need per-row reconstruction
        that ``materialize_range`` does not do, so those fall back to a full
        ``materialize``. The common streamed collection (a code/worker output
        of N flat rows, ``artifacts: []``) IS eligible — the case that matters
        for large loops.

        SA-1977 — this answers *"can it be read by row range?"*, a question about
        STORAGE. It used to be read as *"is it a flat LIST?"*, quietly making it
        the second of five places that inferred the root kind — and the pointer it
        returned for a 1-row file then reached ``orchestration_engine`` as
        ``items``, which indexed it and raised ``KeyError: 0``. The kind question
        is ``value_kind.is_collection``; keep the two apart.
        """
        return bool(
            is_file_ref(value)
            and value.get("file_id")
            and not value.get("artifacts")
        )

    async def materialize_range(
        self, file_ref: dict[str, Any], skip: int, top: int,
    ) -> list[dict[str, Any]]:
        """Return rows ``[skip, skip+top)`` of a flat file-backed list without
        downloading the whole file — via an OData ``$skip``/``$top`` query.

        Caller must gate on :meth:`is_lazy_range_eligible`; for a ref with
        artifacts this would silently drop nested data. Each row is
        reconstructed with the SAME ``_unflatten`` (column_types casting +
        dot-key un-nesting) that a full ``materialize`` applies via
        ``csv_to_dict``, so a lazy row is byte-identical to the eager one; the
        OData-added ``_row_index`` and synthetic ``__row_id`` /
        ``__parent_row_id`` columns are dropped. Returns ``[]`` on no file_id /
        empty range.
        """
        file_id = file_ref.get("file_id", "")
        if not file_id:
            return []
        result = await self._file_client.query_file_data(
            file_id, {"$skip": str(int(skip)), "$top": str(int(top))},
        )
        rows = result.get("data") or []
        column_types = file_ref.get("column_types")
        # SA-1977 A4 — a ``list[scalar]`` is stored as N rows of one reserved
        # ``__value`` column, so the LAZY path must unwrap it exactly like the
        # eager one. Without this, a lazy loop over [1,2,3] would hand the body
        # ``{"__value": 1}`` while the inline path hands it ``1``.
        #
        # Gated on the DECLARATION, never on the row's columns: a node output that
        # is a list of one-key ``{"__value": …}`` dicts is an ordinary list of
        # objects, and sniffing would unwrap it into scalars and destroy it.
        unwrap = ref_uses_value_column(file_ref)
        reconstructed = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rec = self._csv._unflatten(
                {k: v for k, v in row.items() if k != "_row_index"}, column_types,
            )
            reconstructed.append(self._csv._unwrap_value(rec) if unwrap else rec)
        return self._strip_synthetic_columns(reconstructed)

    @classmethod
    def _strip_synthetic_columns(cls, data: Any) -> Any:
        """Drop file-service synthetic columns from materialized rows.

        Returns the original ``dict``/``list`` shape with ``__row_id`` /
        ``__parent_row_id`` removed from each row dict. Non-row values pass
        through unchanged.
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in cls._SYNTHETIC_COLUMNS}
        if isinstance(data, list):
            return [
                {k: v for k, v in row.items() if k not in cls._SYNTHETIC_COLUMNS}
                if isinstance(row, dict) else row
                for row in data
            ]
        return data

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # SA-1905 — the Arrow sidecar
    # ------------------------------------------------------------------

    def _codec_args(self) -> tuple:
        """The codec argument, or NOTHING at all.

        Returning an empty tuple when no codec is configured is the whole point:
        during the rollout a control plane can hold a NEW config and an OLD
        wheel, whose ``jsonl_to_*`` take no ``codec`` parameter. Passing
        ``"none"`` unconditionally would raise ``TypeError`` there — and both
        call sites catch broadly and degrade to CSV-only, so the sidecar would
        stop being written everywhere with nothing going red.

        Pinned by ``test_arrow_codec_plumbing.py``.
        """
        return (self._arrow_codec,) if self._arrow_codec else ()

    @staticmethod
    def _arrow_available() -> bool:
        """The wheel is built AND new enough to carry the nested reader."""
        return _STREAMER is not None and hasattr(_STREAMER, "arrow_bytes_to_rows")

    async def _upload_arrow(self, arrow_bytes: bytes, filename: str) -> str:
        """Upload the sidecar.

        Deliberately NOT through ``_upload_csv``: ``inject_row_ids`` is a CSV
        byte operation and would corrupt Arrow IPC. See ``ARROW_CONTENT_TYPE``
        for why the name and mime type are load-bearing.
        """
        result = await self._file_client.upload_file(
            arrow_bytes, filename, ARROW_CONTENT_TYPE,
            idempotency_key=_content_key(arrow_bytes),
        )
        return result.get("file_id", "")

    def _arrow_worth_writing(self, rows: Any) -> bool:
        """Same estimate-from-row-0 rule ``_should_stream`` uses, deliberately:
        a second sizing rule would be a second thing to keep in step.

        Sized by ``ARROW_SIDECAR_MIN_BYTES``, not by the streaming threshold.
        Borrowing that one was a mistake with a measurable consequence — see
        ``_arrow_min_bytes``.
        """
        if not (self._arrow_write_enabled and self._arrow_available()):
            return False
        if not hasattr(_STREAMER, "jsonl_to_nested_arrow"):
            return False
        if not (isinstance(rows, list) and rows):
            return False
        try:
            per_row = len(json.dumps(rows[0], default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return False
        return per_row * len(rows) >= self._arrow_min_bytes

    async def _write_nested_sidecar(self, key: str, rows: list) -> tuple[str, int]:
        """Write and upload the nested Arrow mirror of *rows*.

        Fed the ORIGINAL, pre-prune rows on purpose: that is what lets one file
        hold the whole nested value, and it is the only reason the read path can
        skip the ``__parent_row_id`` join entirely.

        Returns ``("", 0)`` on any failure. The CSV is always written and always
        the fallback, so a sidecar problem must never change the stored value —
        and never silently either, because a sidecar that always fails looks
        exactly like one nobody enabled.
        """
        if not self._arrow_worth_writing(rows):
            return "", 0
        tmpdir = tempfile.mkdtemp(prefix="wcp-arrow-")
        try:
            jsonl_path = os.path.join(tmpdir, "in.jsonl")
            arrow_path = os.path.join(tmpdir, "out.arrow")
            with open(jsonl_path, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, default=str))
                    fh.write("\n")
            _STREAMER.jsonl_to_nested_arrow(jsonl_path, arrow_path, *self._codec_args())
            with open(arrow_path, "rb") as fh:
                blob = fh.read()
            if not blob:
                return "", 0
            file_id = await self._upload_arrow(blob, f"{key}.arrow")
            return (file_id, len(blob)) if file_id else ("", 0)
        except Exception as exc:
            _logger.warning(
                "SA-1905 nested arrow sidecar failed for key=%s (%s); "
                "the CSV is unaffected", key, exc,
            )
            return "", 0
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _arrow_outcome(self, outcome: str, cause: str = "") -> None:
        """Count one sidecar read. The label is the point: a fallback nobody can
        see is indistinguishable from a fast path, and a systematically broken
        sidecar that always falls back looks exactly like one nobody wrote."""
        if self._monitor is None:
            return
        try:
            # ALWAYS both keys, ``cause=""`` when there is none.
            #
            # ``PrometheusMonitor._get_counter`` freezes a metric's label set on
            # the FIRST call after a restart and returns None — silently — for
            # every later call whose keys differ. `hit` carries no cause, so it
            # won the race and every labelled outcome after it was dropped on
            # the floor: a live probe recorded `hit` with no verdict at all,
            # and the `skip` causes have almost certainly never once been
            # recorded in production.
            self._monitor.increment(
                "arrow_read_total", tags={"outcome": outcome, "cause": cause},
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass

    def _arrow_ineligible_cause(self, file_ref: dict[str, Any]) -> str:
        """Why this read will not consult the sidecar — ``""`` when it will.

        Returns a CAUSE rather than a bool because the first shadow soak could
        not tell three states apart, all of which showed as no counter at all:
        the flag being off, no sidecar having been written, and the read never
        reaching ``materialize``. A silent skip is indistinguishable from a
        clean run, which is the one thing an observability change must not be.
        """
        if self._arrow_read_mode == _ARROW_OFF:
            return "read_disabled"
        if not self._arrow_available():
            return "wheel_missing"
        if not file_ref.get("arrow_file_id"):
            return "no_sidecar"
        if not str(file_ref.get("content_type") or "").lower().startswith("text/csv"):
            return "not_csv"
        return ""

    def _arrow_eligible(self, file_ref: dict[str, Any]) -> bool:
        """Ref-local and O(1). Every check is a reason, not a precaution."""
        if self._arrow_read_mode == _ARROW_OFF or not self._arrow_available():
            return False
        if not file_ref.get("arrow_file_id"):
            return False
        # HTTP-node payloads are stored verbatim and read by the JSON sniff
        # below; they are not tabular and have no sidecar.
        if not str(file_ref.get("content_type") or "").lower().startswith("text/csv"):
            return False
        # NO size comparison against ``size_bytes``. That guard was in the plan
        # and is wrong twice: ``size_bytes`` is the MAIN CSV only, while the
        # sidecar mirrors the WHOLE tree, so the comparison is between different
        # things; and Arrow IPC carries a fixed framing cost, so on a small
        # value the sidecar is legitimately larger and the guard would refuse
        # every one of them — silently, which is the worst way for a feature to
        # not work.
        #
        # The real protection is exact and already in place: the throwaway
        # ``_ByteBudget`` in ``_try_materialize_arrow`` caps the bytes actually
        # pulled at ``max_bytes``, the same ceiling the CSV path answers to.
        return True

    async def _try_materialize_arrow(
        self, file_ref: dict[str, Any], max_bytes: int, context: str,
    ) -> Any:
        """The Arrow read, or ``_ARROW_MISS``.

        Never raises for a sidecar problem: the caller then takes the CSV path
        it would have taken anyway. ``MaterializeTooLargeError`` DOES propagate,
        because the throwaway ``_ByteBudget`` below caps this read at the SAME
        ``max_bytes`` the CSV path answers to — so a value too large here is too
        large either way, and swallowing it would turn the OOM guard off for
        exactly the values it exists for.

        (This paragraph used to claim ``_arrow_eligible`` had established the
        sidecar was no larger than the CSV tree. It does not, deliberately —
        see the comment there. Measured 2026-08-19: a sidecar is routinely
        LARGER than its CSV tree, 481 042 B against 329 256 B, so a reader
        trusting that sentence would have been reasoning from a false premise.)
        """
        cause = self._arrow_ineligible_cause(file_ref)
        if cause:
            # Counted, not silent. "read_disabled" is the only one that is
            # simply the feature being off; the rest are things somebody wants
            # to see on a dashboard during a soak.
            if cause != "read_disabled":
                self._arrow_outcome("skip", cause)
            return _ARROW_MISS
        if not self._arrow_eligible(file_ref):
            self._arrow_outcome("skip", "ineligible")
            return _ARROW_MISS
        try:
            blob = await self._file_client.download_file(str(file_ref["arrow_file_id"]))
            if not blob:
                self._arrow_outcome("fallback", "missing_file")
                return _ARROW_MISS
            # A budget of its own: ``_ByteBudget.add`` has no rollback, so
            # charging an aborted attempt to the caller's budget would make the
            # CSV fallback raise where CSV alone would have passed.
            _ByteBudget(max_bytes, file_ref, context or "arrow-sidecar").add(len(blob))
            rows = _STREAMER.arrow_bytes_to_rows(blob, self._arrow_strict_parity)
            value = self._strip_synthetic_columns(
                self._csv.rows_to_value(
                    rows, kind_of(file_ref), declared_item_kind(file_ref),
                )
            )
            self._arrow_outcome("hit")
            return value
        except MaterializeTooLargeError:
            raise
        except Exception as exc:  # pragma: no cover - fallback is the contract
            self._arrow_outcome("fallback", "read_error")
            _logger.warning(
                "SA-1905 arrow sidecar read failed for file_id=%s (%s); "
                "falling back to CSV", file_ref.get("arrow_file_id"), exc,
            )
            return _ARROW_MISS

    def _shadow_compare(
        self, file_ref: dict[str, Any], arrow_value: Any, csv_value: Any,
    ) -> None:
        """Diff the two readings and return nothing — the CSV value always wins
        in shadow mode.

        The comparator is a canonical JSON dump, NOT ``==``: in Python
        ``1 == 1.0`` and ``True == 1``, so an equality assertion would certify
        exactly the type divergences this is looking for.
        """
        try:
            same = json.dumps(arrow_value, sort_keys=True, default=repr) == json.dumps(
                csv_value, sort_keys=True, default=repr
            )
        except Exception:  # pragma: no cover - unserializable is a mismatch
            same = False
        if same:
            self._arrow_outcome("shadow_match")
            return
        self._arrow_outcome("mismatch", _divergence_kind(arrow_value, csv_value))
        _logger.warning(
            "SA-1905 arrow/csv shadow MISMATCH file_id=%s arrow_file_id=%s "
            "value_kind=%s item_kind=%s — the CSV value was returned",
            file_ref.get("file_id"), file_ref.get("arrow_file_id"),
            kind_of(file_ref), declared_item_kind(file_ref),
        )

    async def _upload_csv(self, csv_bytes: bytes, filename: str) -> str:
        """Inject the synthetic ``__row_id`` column, then upload.

        Every CSV the file service stores must carry an integer-valued
        ``__row_id`` (see ``csv_ops`` — "the file service requires
        ``__row_id`` to be integer-valued"). The PATCH/edit and
        schema-provisioning paths already inject it; ``store_data`` is
        the only upload path that historically skipped it, so a CSV with
        no ``__row_id`` reached the file service and was mangled at the
        row-id injection seam on ingest. Funnelling all uploads through
        here guarantees the column is present. The content key is hashed
        over the *injected* bytes so dedupe stays byte-exact.
        """
        csv_bytes = CsvDocument(csv_bytes).inject_row_ids()
        upload_result = await self._file_client.upload_file(
            csv_bytes, filename, "text/csv",
            idempotency_key=_content_key(csv_bytes),
        )
        return upload_result.get("file_id", "")

    @staticmethod
    def _is_value_root(data: Any) -> bool:
        """True when this value is STORED in the reserved ``__value`` column.

        These are the shapes ``_flatten`` cannot touch (it calls ``.items()``), so
        before SA-1977 A4 they raised at write time. An EMPTY list is not one of
        them — it takes the ordinary list path and is stored as a 0-row array.

        The list arm is deliberately expressed as ``item_kind != object`` — the
        SAME predicate the reader uses (``uses_value_column``) — rather than as an
        independent "any element that isn't a dict" scan. Those two phrasings look
        identical and are not: a sparse ``[{...}, None, {...}]`` is stored in
        ``__value`` by the first and declared ``array<object>`` by the second, and
        then the reader refuses to unwrap what the writer wrapped. One rule, one
        expression, or they drift.
        """
        if isinstance(data, (list, tuple)):
            return bool(data) and item_kind_of_inline(data) != OBJECT
        return not isinstance(data, dict)

    async def _store_values(
        self, key: str, values: list[Any], *, root_is_list: bool,
    ) -> dict[str, Any]:
        """Store a scalar / list-of-scalar root via the reserved ``__value``
        column (SA-1977 A4). Still CSV, still N rows — so still OData-pageable,
        still lazily row-readable, still steward-editable."""
        csv_bytes, _fn, row_count = self._csv.values_to_csv(values)
        value_kind = ARRAY if root_is_list else kind_of_inline(values[0])
        item_kind = item_kind_of_inline(values) if root_is_list else ""
        if not csv_bytes:
            return build_file_ref(
                file_id="", row_count=0, columns=[],
                value_kind=value_kind, item_kind=item_kind,
            )
        file_id = await self._upload_csv(csv_bytes, f"{key}.csv")
        if not file_id:
            _logger.warning("File upload returned no file_id for key=%s", key)
            return build_file_ref(
                file_id="", row_count=0, columns=[],
                value_kind=value_kind, item_kind=item_kind,
            )
        return build_file_ref(
            file_id=file_id,
            content_type="text/csv",
            row_count=row_count,
            columns=[VALUE_COLUMN],
            column_types=self._csv.infer_value_column_types(values),
            value_kind=value_kind,
            item_kind=item_kind,
        )

    async def _store_list(self, key: str, data: list) -> dict[str, Any]:
        """Store a list as a multi-row CSV."""
        csv_bytes, filename, row_count = self._csv.dict_to_csv(data)
        # This path is reached only when every element is a dict (``_is_value_root``
        # sent the others to ``_store_values``), so the element kind is known
        # without scanning N rows. An EMPTY list declares NO element kind — there
        # is nothing to observe, and claiming one would be a guess.
        item_kind = OBJECT if data else ""
        if not csv_bytes:
            # An empty list. It has no bytes — so ``value_kind`` is the ONLY
            # thing that distinguishes this ref from an empty ``{}``.
            return build_file_ref(
                file_id="", row_count=0, columns=[],
                value_kind=ARRAY, item_kind=item_kind,
            )
        filename = f"{key}.csv"
        file_id = await self._upload_csv(csv_bytes, filename)
        if not file_id:
            # Upload failed (defensive, should-never-happen; already logged).
            # Return an empty ref — same shape as the no-bytes branch above —
            # rather than leaking the raw list, which would break the dict
            # return contract and surface downstream as non-file-backed data.
            _logger.warning("File upload returned no file_id for key=%s", key)
            return build_file_ref(
                file_id="", row_count=0, columns=[],
                value_kind=ARRAY, item_kind=item_kind,
            )
        return build_file_ref(
            file_id=file_id,
            content_type="text/csv",
            row_count=row_count,
            columns=self._extract_columns(data),
            column_types=self._csv.infer_column_types(data),
            value_kind=ARRAY,
            item_kind=item_kind,
        )

    def _should_stream(self, data: Any) -> bool:
        """True when a large list should take the streaming CSV path.

        Gated on the feature flag AND the native producer being importable.
        Size is *estimated* cheaply from the first row (one row serialized ×
        count) so the decision itself never serializes the whole payload.

        SA-1977 — the gate no longer requires a list of DICTS. A 500 MB list of
        strings blows up ``values_to_csv`` in RAM exactly like a list of objects
        blew up ``dict_to_csv``; the streamer handles both (it writes the
        reserved ``__value`` column for a non-object line).
        """
        if not (self._chunked_enabled and _STREAMER is not None):
            return False
        if not (isinstance(data, list) and data):
            return False
        try:
            per_row = len(json.dumps(data[0], default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return False
        return per_row * len(data) >= self._chunking_threshold

    async def _store_list_streaming(self, key: str, data: list) -> dict[str, Any]:
        """Stream a large list-of-dicts to CSV via the native producer.

        Writes JSONL to a temp file one row at a time (bounded RAM), converts
        it with the native two-pass streamer — no in-RAM column union, no
        5.5-9.3× ``dict_to_csv`` blow-up, no 128 MB cell limit — then uploads
        the CSV. The native CSV already carries the synthetic integer
        ``__row_id``, so it is upload-ready without a row-id injection pass.
        Falls back to the in-RAM ``_store_list`` on any streaming failure so a
        missing/older wheel or a malformed row never drops data.
        """
        tmpdir = tempfile.mkdtemp(prefix="wcp-chunk-")
        try:
            jsonl_path = os.path.join(tmpdir, f"{key}.jsonl")
            csv_path = os.path.join(tmpdir, f"{key}.csv")
            try:
                with open(jsonl_path, "w", encoding="utf-8") as fh:
                    for row in data:
                        fh.write(json.dumps(row, default=str))
                        fh.write("\n")
                # SA-1977 — the streamer now also reports the element kind it
                # observed, so the streamed ref declares exactly what the in-RAM
                # writer would have declared for the same value.
                arrow_path = os.path.join(tmpdir, f"{key}.arrow")
                arrow_written = False
                if self._arrow_write_enabled and self._arrow_available() and hasattr(
                    _STREAMER, "jsonl_to_csv_and_nested_arrow"
                ):
                    try:
                        # One scan, two sketches, two writers. The CSV bytes
                        # are asserted byte-identical to the CSV-only path in
                        # the crate, which is what lets the sidecar ship.
                        columns, column_types, row_count, item_kind = (
                            _STREAMER.jsonl_to_csv_and_nested_arrow(
                                jsonl_path, csv_path, arrow_path,
                                *self._codec_args(),
                            )
                        )
                        arrow_written = True
                    except Exception as exc:
                        # Degrade to CSV-only, NOT to the in-RAM path: losing
                        # the sidecar costs speed, losing the streamer costs
                        # the whole reason this branch exists.
                        _logger.warning(
                            "SA-1905 nested arrow sidecar failed for key=%s "
                            "(%s); writing the CSV alone", key, exc,
                        )
                if not arrow_written:
                    columns, column_types, row_count, item_kind = _STREAMER.jsonl_to_csv(
                        jsonl_path, csv_path,
                    )
                with open(csv_path, "rb") as fh:
                    csv_bytes = fh.read()
            except Exception as exc:
                _logger.warning(
                    "Streaming CSV conversion failed for key=%s (%s); "
                    "falling back to the in-RAM path", key, exc,
                )
                # The in-RAM twin of whichever shape this is — a value root must
                # NOT fall back into ``_store_list``, which is the path that
                # raises AttributeError on a non-mapping.
                if self._is_value_root(data):
                    return await self._store_values(key, data, root_is_list=True)
                return await self._store_list(key, data)

            if not csv_bytes:
                return build_file_ref(
                    file_id="", row_count=0, columns=[], value_kind=ARRAY,
                )
            upload_result = await self._file_client.upload_file(
                csv_bytes, f"{key}.csv", "text/csv",
                idempotency_key=_content_key(csv_bytes),
            )
            file_id = upload_result.get("file_id", "")
            if not file_id:
                _logger.warning("Streaming upload returned no file_id for key=%s", key)
                return build_file_ref(
                    file_id="", row_count=0, columns=[], value_kind=ARRAY,
                )
            arrow_file_id, arrow_size = "", 0
            if arrow_written:
                try:
                    with open(arrow_path, "rb") as fh:
                        arrow_bytes = fh.read()
                    if arrow_bytes:
                        arrow_file_id = await self._upload_arrow(
                            arrow_bytes, f"{key}.arrow",
                        )
                        arrow_size = len(arrow_bytes) if arrow_file_id else 0
                except Exception as exc:
                    _logger.warning(
                        "SA-1905 arrow sidecar upload failed for key=%s (%s); "
                        "the CSV ref is unaffected", key, exc,
                    )
                    arrow_file_id, arrow_size = "", 0

            # A JSONL file IS a list of its lines, so the root is always an array;
            # the element kind comes from the streamer's own PASS-1 observation.
            return build_file_ref(
                file_id=file_id, content_type="text/csv",
                row_count=row_count, columns=columns, column_types=column_types,
                value_kind=ARRAY, item_kind=item_kind,
                arrow_file_id=arrow_file_id, arrow_size_bytes=arrow_size,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _store_dict(self, key: str, data: dict[str, Any]) -> dict[str, Any]:
        """Store a dict as a single-row CSV."""
        csv_bytes, filename, row_count = self._csv.dict_to_csv(data)
        if not csv_bytes:
            return build_file_ref(
                file_id="", row_count=0, columns=[], value_kind=OBJECT,
            )
        filename = f"{key}.csv"
        file_id = await self._upload_csv(csv_bytes, filename)
        if not file_id:
            _logger.warning("File upload returned no file_id for key=%s", key)
            return data
        return build_file_ref(
            file_id=file_id,
            content_type="text/csv",
            row_count=row_count,
            columns=self._extract_columns(data),
            column_types=self._csv.infer_column_types(data),
            value_kind=OBJECT,
        )

    # ------------------------------------------------------------------
    # SA-1910 — recursive nested-array split / re-join
    # ------------------------------------------------------------------

    async def _store_rows_recursive(
        self, key: str, rows: list[dict], as_list: bool, root: bool = True,
    ) -> dict[str, Any]:
        """Store *rows* as the nested-array CSV tree.

        Two writers, one tree. The crate's ``jsonl_split_recursive`` is tried
        first when ``NATIVE_RECURSIVE_SPLIT`` is on; ``_store_rows_python``
        below is both the default and the fallback.

        The fallback is not decoration. A native attempt that fails PART WAY
        has already uploaded some children, and the Python retry uploads the
        whole tree again — so a failure costs orphaned files, not data. That is
        the right trade (the alternative is failing a run whose output is
        already computed), but it is the reason this is logged at warning and
        not swallowed.
        """
        if root and self._native_split:
            if _STREAMER is None or not hasattr(_STREAMER, "jsonl_split_recursive"):
                # The flag is on but the wheel cannot serve it. A DEPLOYMENT
                # fault, and it must not read as "nothing tried the native
                # path" — that is indistinguishable from the flag being off.
                self._split_outcome("fallback", "no_splitter")
            else:
                try:
                    ref = await self._store_rows_native(key, rows, as_list)
                    self._split_outcome("native")
                    return ref
                except Exception as exc:
                    self._split_outcome("fallback", type(exc).__name__)
                    _logger.warning(
                        "SA-1910 native split failed for key=%s (%s); falling "
                        "back to the Python writer", key, exc,
                    )
        return await self._store_rows_python(key, rows, as_list, root)

    def _split_outcome(self, outcome: str, cause: str = "") -> None:
        """Count one tree write.

        Added BEFORE the flag was turned on, not after, because the last
        feature soaked without a counter measured nothing and looked exactly
        like a clean run: "no fallbacks" and "the flag never took effect" were
        the same empty graph. Nothing is emitted while the flag is off — that
        is every deployment which has not opted in, and it would be a permanent
        meaningless series on every dashboard.

        ``cause`` on a fallback is the exception CLASS, not its message: a
        message carries file ids and keys, and a metric label with unbounded
        cardinality is how a metrics backend falls over.
        """
        if self._monitor is None:
            return
        try:
            # Both keys, always — see ``_arrow_outcome``. This counter has the
            # same shape (``native`` carries no cause, ``fallback`` does), so
            # every fallback would have been dropped while the 285 successes
            # were recorded: exactly the reading that looks like "it never
            # fails".
            self._monitor.increment(
                "native_split_total", tags={"outcome": outcome, "cause": cause},
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass

    async def gc_files(self, file_ids: list[str], *, reason: str = "") -> None:
        """Best-effort delete of files nothing points at.

        Deliberately best-effort and awaited rather than fire-and-forget: these
        callers are already on a failure path, and a delete that raises there
        would turn "some wasted storage" into "a lost run". ``ORPHAN_GC_ENABLED``
        is the same kill switch ``edit_node_data`` uses, so a debugging session
        that wants every intermediate to stay queryable turns off one thing.
        """
        ids = [f for f in dict.fromkeys(file_ids or []) if f]
        if not ids:
            return
        if not self._orphan_gc_enabled:
            _logger.debug("orphan GC disabled — keeping %d file_ids", len(ids))
            return
        for fid in ids:
            try:
                await self._file_client.delete_file(fid)
            except Exception as exc:  # pragma: no cover - GC must never raise
                _logger.debug("orphan GC failed for %s (%s): %s", fid, reason, exc)
        _logger.info(
            "orphan GC removed %d file(s) — %s", len(ids), reason or "unreferenced",
        )

    async def _store_rows_native(
        self, key: str, rows: list[dict], as_list: bool,
    ) -> dict[str, Any]:
        """Build the whole tree in the crate, then upload it.

        One call covers every level — the recursion lives in Rust — so this is
        only ever entered at the root.
        """
        tmpdir = tempfile.mkdtemp(prefix="cp-split-")
        # Every file this attempt creates. If it dies part way the Python
        # writer re-uploads the WHOLE tree, so without this the half-tree stays
        # on the file service forever with nothing pointing at it.
        uploaded: list[str] = []
        try:
            jsonl_path = os.path.join(tmpdir, "in.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, default=str))
                    fh.write("\n")
            manifest = _STREAMER.jsonl_split_recursive(jsonl_path, tmpdir)
            ref = await self._ref_from_manifest(manifest, key, as_list, uploaded)
            # SA-1905 — ONE sidecar, at the root, from the pre-prune rows;
            # identical to the Python path (a child gets none, because the root
            # Arrow file already holds the whole tree).
            arrow_id, arrow_size = await self._write_nested_sidecar(key, rows)
            if arrow_id:
                ref["arrow_file_id"] = arrow_id
                ref["arrow_size_bytes"] = arrow_size
            return ref
        except Exception:
            await self.gc_files(uploaded, reason="native split failed part way")
            raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _ref_from_manifest(
        self, node: dict[str, Any], key: str, as_list: bool,
        uploaded: list[str] | None = None,
    ) -> dict[str, Any]:
        """Walk one manifest node into a file_ref, depth-first.

        Deliberately still ``build_file_ref`` / ``add_artifact`` rather than
        spelling the dicts here: the manifest decides what the TREE is, this
        decides what a ref LOOKS like, and only the first of those was worth
        moving out of Python.

        ``size_bytes`` comes from the manifest, which measures the file BEFORE
        ``_upload_csv``'s ``inject_row_ids`` round-trip — the same point the
        Python writer measures at (``len(csv_bytes)`` off ``dict_to_csv``), so
        the number means the same thing on both paths. The manifest's
        ``content_key`` is deliberately NOT used here: ``_upload_csv`` hashes
        the INJECTED bytes so dedupe stays byte-exact against what is stored.

        An upload that answers 2xx with no ``file_id`` yields ``file_id=""``
        and is NOT caught — deliberately, because ``_store_rows_python`` does
        exactly the same thing (see the identical line there) and inventing a
        guard on one writer only would make the two disagree about failure
        while agreeing about success. It is a real shared weakness, pinned in
        ``test_native_split_parity.py``; a transport error still raises, so
        this is only the "success-shaped empty response" case the file-service
        client already logs as an error.
        """
        with open(node["csv_path"], "rb") as fh:
            csv_bytes = fh.read()
        file_id = await self._upload_csv(csv_bytes, f"{key}.csv") if csv_bytes else ""
        if file_id and uploaded is not None:
            uploaded.append(file_id)
        ref = build_file_ref(
            file_id=file_id, content_type="text/csv",
            row_count=node["row_count"],
            size_bytes=node["size_bytes"],
            columns=list(node["columns"]),
            column_types=dict(node["column_types"]),
            value_kind=ARRAY if as_list else OBJECT,
            item_kind=OBJECT if as_list else "",
        )
        for child in node["children"]:
            child_ref = await self._ref_from_manifest(
                child, f"{key}-{child['name']}", as_list=True, uploaded=uploaded,
            )
            art = add_artifact(
                ref, file_id=child_ref.get("file_id", ""), name=child["name"],
                content_type="text/csv", artifact_type="nested_array",
                parent_row_key_column="__parent_row_id",
                column_types=child_ref.get("column_types"),
                size_bytes=child_ref.get("size_bytes", 0),
            )
            art["row_count"] = child_ref.get("row_count", 0)
            art["value_kind"] = ARRAY
            art["item_kind"] = OBJECT
            art["columns"] = list(child_ref.get("columns") or [])
            # ``columns`` arrives after add_artifact, so its hash is stale.
            refresh_schema_hash(art)
            art["artifacts"] = child_ref.get("artifacts", [])
        return ref

    async def _store_rows_python(
        self, key: str, rows: list[dict], as_list: bool, root: bool = True,
    ) -> dict[str, Any]:
        """Store *rows* with nested array-of-dicts (any depth) pulled into child
        artifacts. Each child row is stamped ``__parent_row_id`` = its parent's
        row index (which becomes the parent's injected ``__row_id``), and each
        child is itself stored recursively (grandchildren split too).

        ``as_list=False`` stores a single root dict (1-row main CSV);
        ``as_list=True`` stores a multi-row table (a list input or a child).
        """
        pruned_rows: list[dict] = []
        per_path: dict[str, list[dict]] = {}
        for idx, row in enumerate(rows):
            pruned, arrays = self._csv.prune_arrays_of_dicts(row)
            # Stamp our OWN 0-based __row_id so it matches the children's
            # __parent_row_id (below). ``_upload_csv``'s inject_row_ids skips
            # rows that already carry a __row_id, so its 1-based scheme doesn't
            # interfere. Both keys are stripped on materialize.
            pruned["__row_id"] = idx
            pruned_rows.append(pruned)
            for path, arr in arrays.items():
                bucket = per_path.setdefault(path, [])
                for child in arr:
                    c = dict(child)
                    c["__parent_row_id"] = idx
                    bucket.append(c)

        payload: Any = pruned_rows if as_list else (pruned_rows[0] if pruned_rows else {})
        csv_bytes, _fn, row_count = self._csv.dict_to_csv(payload)
        file_id = await self._upload_csv(csv_bytes, f"{key}.csv") if csv_bytes else ""
        ref = build_file_ref(
            file_id=file_id, content_type="text/csv", row_count=row_count,
            size_bytes=len(csv_bytes or b""),
            columns=self._extract_columns(payload),
            column_types=self._csv.infer_column_types(payload),
            # SA-1977 — ``as_list`` IS the root kind. It was already a parameter
            # here; it was simply never written down.
            value_kind=ARRAY if as_list else OBJECT,
            item_kind=OBJECT if as_list else "",
        )
        for path, child_rows in per_path.items():
            child_ref = await self._store_rows_python(
                f"{key}-{path}", child_rows, as_list=True, root=False,
            )
            art = add_artifact(
                ref, file_id=child_ref.get("file_id", ""), name=path,
                content_type="text/csv", artifact_type="nested_array",
                parent_row_key_column="__parent_row_id",
                column_types=child_ref.get("column_types"),
                # SA-1942 — the size the OOM guard needs. Left at 0 before, so
                # ``probe_size_bytes``'s "use the declared size, skip the HEAD"
                # branch could never fire on a recursive split: every artifact
                # cost a HEAD round-trip, serially, and the guard then measured
                # only the top level (35% of the real bytes on a 41-file tree).
                size_bytes=child_ref.get("size_bytes", 0),
            )
            art["row_count"] = child_ref.get("row_count", 0)
            art["value_kind"] = ARRAY  # a nested_array child is always a table
            art["item_kind"] = OBJECT
            # The declared column list. ``build_descriptor_tree`` reads exactly
            # this to tell the FE what a nested table holds, and it was empty
            # for every recursive-split artifact.
            art["columns"] = list(child_ref.get("columns") or [])
            # ``columns`` arrives after add_artifact, so the hash it wrote
            # is stale. A stale pointer resolves to a DIFFERENT schema.
            refresh_schema_hash(art)
            # Carry grandchild artifacts so the file_ref tree is recursive.
            art["artifacts"] = child_ref.get("artifacts", [])

        # SA-1905 — ONE sidecar, at the root, from the pre-prune rows. A child
        # gets none: the root Arrow file already holds the whole tree, and a
        # per-node sidecar would re-encode the same data once per split path.
        if root:
            arrow_id, arrow_size = await self._write_nested_sidecar(key, rows)
            if arrow_id:
                ref["arrow_file_id"] = arrow_id
                ref["arrow_size_bytes"] = arrow_size
        return ref

    @staticmethod
    def _to_int(v: Any) -> Any:
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    async def _download_many(self, file_ids: list[str]) -> dict[str, Any]:
        """Fetch DISTINCT *file_ids* with bounded concurrency.

        Returns id → bytes, ``None`` when the file was missing, or the
        exception it raised. The exception is handed back rather than raised so
        each caller keeps its OWN error policy: the recursive join propagates a
        failure (truncated data is worse than none), while the ``array_field``
        re-attach warns and carries on. A shared helper that picked one of those
        would quietly change the other.
        """
        out: dict[str, Any] = {}
        sem = asyncio.Semaphore(_PREFETCH_CONCURRENCY)

        async def _one(file_id: str) -> None:
            async with sem:
                try:
                    out[file_id] = await self._file_client.download_file(file_id)
                except BaseException as exc:  # noqa: BLE001 - returned to caller
                    out[file_id] = exc

        await asyncio.gather(*(_one(f) for f in dict.fromkeys(file_ids)))
        return out

    @staticmethod
    def _tree_nodes(ref: dict[str, Any]) -> list[dict[str, Any]]:
        """Every node of a recursive-split tree that owns a CSV, root first.

        Pure walk over the ref — no I/O. This is the fact the serial download
        loop never used: ``_store_rows_recursive`` hangs each child's own
        artifacts off its parent, so the ENTIRE set of ``file_id``s is known
        before the first byte is fetched.
        """
        out: list[dict[str, Any]] = []

        def walk(node: dict[str, Any]) -> None:
            if node.get("file_id"):
                out.append(node)
            for art in node.get("artifacts") or []:
                if art.get("artifact_type") == "nested_array":
                    walk(art)

        walk(ref)
        return out

    async def _prefetch_tree(
        self, ref: dict[str, Any], budget: "_ByteBudget | None",
    ) -> dict[str, bytes]:
        """Download every CSV in the recursive tree CONCURRENTLY.

        The ``__parent_row_id`` join needs the bytes, not the order they arrive
        in, and no child's identity depends on its parent's CONTENT — so the
        walk awaited one download at a time purely because that is the shape a
        recursive tree walk takes. Measured on a wide graph: 41 serial
        round-trips, 2.4s at 50ms each, for 22ms of actual work.

        The ``_ByteBudget`` bound is preserved and, where the refs allow it,
        TIGHTENED: every node that declares ``size_bytes`` is charged BEFORE
        anything is scheduled, so an oversized tree is now refused without a
        single download — the old post-download check could only fire once the
        bytes were already in RAM. Nodes that declare nothing (refs written
        before ``_store_rows_recursive`` stamped it) are charged on arrival as
        before; on that legacy path the worst-case overshoot is
        ``_PREFETCH_CONCURRENCY`` chunks instead of one, and the fetch stops
        scheduling as soon as the cap is crossed.

        Duplicate ``file_id``s are downloaded once — content-idempotent uploads
        make byte-identical children share an id — but charged per occurrence,
        because each occurrence is parsed into its own structure.
        """
        nodes = self._tree_nodes(ref)
        if not nodes:
            return {}

        undeclared: dict[str, int] = {}
        declared_total = 0
        for node in nodes:
            fid = node.get("file_id", "")
            size = int(node.get("size_bytes") or 0)
            if size > 0:
                declared_total += size
            else:
                undeclared[fid] = undeclared.get(fid, 0) + 1
        if budget is not None and declared_total:
            budget.add(declared_total)  # raises BEFORE any I/O

        blobs: dict[str, bytes] = {}
        stop: list[BaseException] = []
        sem = asyncio.Semaphore(_PREFETCH_CONCURRENCY)

        async def _fetch(file_id: str) -> None:
            async with sem:
                if stop:  # cap already blown / a peer failed — schedule no more
                    return
                try:
                    data = await self._file_client.download_file(file_id)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    stop.append(exc)
                    return
                if not data:
                    return
                blobs[file_id] = data
                repeats = undeclared.get(file_id, 0)
                if budget is not None and repeats:
                    try:
                        budget.add(len(data) * repeats)
                    except MaterializeTooLargeError as exc:
                        stop.append(exc)

        # Every task swallows its own failure, so ``gather`` has nothing to
        # propagate; re-raising here keeps the caller's contract identical to
        # the serial loop's (the first error wins, and it still escapes).
        unique_ids = list(dict.fromkeys(n.get("file_id", "") for n in nodes))
        await asyncio.gather(*(_fetch(f) for f in unique_ids))
        if stop:
            raise stop[0]
        return blobs

    async def _reconstruct_recursive(
        self,
        ref: dict[str, Any],
        budget: "_ByteBudget | None" = None,
        blobs: dict[str, bytes] | None = None,
    ) -> Any:
        """Inverse of :meth:`_store_rows_recursive`: re-attach each
        ``nested_array`` child at its dotted path, grouped by
        ``__parent_row_id`` → the parent's ``__row_id``. Synthetic columns are
        kept for linking and stripped once, deeply, by the caller.

        SA-1942 — an optional ``budget`` accounts every CSV (main + every
        descendant) against the materialize cap so a large recursive split
        raises instead of OOMing. Accounting now happens in
        :meth:`_prefetch_tree`, which fetches the whole tree at once; this walk
        is pure CPU over the bytes it returns."""
        if blobs is None:
            blobs = await self._prefetch_tree(ref, budget)
        file_id = ref.get("file_id", "")
        csv_bytes = blobs.get(file_id) if file_id else None
        # SA-1977 — ``was_list`` used to come from the row count, so a
        # recursively-split list of exactly ONE row rebuilt itself as an object.
        # The declared kind decides it now; a legacy ref keeps the old guess.
        #
        # ``declared_kind``, NOT ``kind_of``: this walk recurses over ARTIFACT
        # dicts as well as the root ref, and an artifact carries no ``__file_ref``
        # marker — so ``kind_of`` would take the inline branch, see a dict, and
        # answer "object" for a child table of N rows, collapsing it to its first
        # row. Node, not value.
        declared = declared_kind(ref)
        rows = (
            self._csv.csv_to_dict(
                csv_bytes, ref.get("column_types"), value_kind=declared,
                item_kind=declared_item_kind(ref),
            )
            if csv_bytes else empty_for_kind(declared)
        )
        was_list = isinstance(rows, list)
        rows_list: list[dict] = rows if was_list else [rows]
        # An only-arrays root has no main CSV; synthesize its single row 0 so
        # its children (parent_row_id=0) still attach.
        if not was_list and not rows_list[0]:
            rows_list = [{"__row_id": 0}]

        for art in ref.get("artifacts", []):
            if art.get("artifact_type") != "nested_array" or not art.get("file_id"):
                continue
            # Yield between children — load-bearing, not tidiness.
            #
            # Prefetching removed the per-file ``await download_file(...)``,
            # which is the point. But those awaits were also the only REAL
            # suspension points in this walk, and awaiting a coroutine that
            # never suspends does not hand the event loop a turn. Without an
            # explicit yield the entire recursion — every parse, every join,
            # and the deep strip after it — runs as ONE uninterruptible block.
            #
            # Measured on a 6000-row graph (worst contiguous block, sampled by
            # a task that re-yields immediately, so a gap IS blocked time):
            #
            #   serial downloads   wall 243/223/219ms   blocks [129, 45, 10]
            #   prefetch, no yield wall 204/189/176ms   blocks [173]
            #   prefetch + yields  wall 199/185/180ms   blocks [99, 42, 27, 11]
            #
            # The CPU work is identical in all three (41.4/86.4/28.8ms vs
            # 39.7/85.5/28.4ms) — only its arrangement against the loop differs.
            # With the yields the control plane gets both: ~20% less wall time
            # AND a shorter worst stall than it had before. Without them it
            # trades a 118-129ms stall for a 156-173ms one, which on a plane
            # pinned at ``instances: 1`` is the wrong direction.
            await asyncio.sleep(0)
            child = await self._reconstruct_recursive(art, None, blobs)
            child_list = child if isinstance(child, list) else ([child] if child else [])
            groups: dict[Any, list] = {}
            for c in child_list:
                if isinstance(c, dict):
                    groups.setdefault(self._to_int(c.get("__parent_row_id")), []).append(c)
            for prow in rows_list:
                rid = self._to_int(prow.get("__row_id"))
                if rid in groups:
                    self._csv.set_by_path(prow, art["name"], groups[rid])

        return rows_list if was_list else rows_list[0]

    @classmethod
    def _strip_synthetic_deep(cls, obj: Any) -> Any:
        """Recursively drop ``__row_id`` / ``__parent_row_id`` at every level."""
        if isinstance(obj, dict):
            return {k: cls._strip_synthetic_deep(v) for k, v in obj.items()
                    if k not in cls._SYNTHETIC_COLUMNS}
        if isinstance(obj, list):
            return [cls._strip_synthetic_deep(x) for x in obj]
        return obj

    @staticmethod
    def _is_array_of_dicts(value: Any) -> bool:
        """Is this field a table of objects (→ its own artifact CSV)?

        SA-1977 — checks EVERY element, not just the first. This was the third
        expression of one rule and the only one that got it wrong::

            value_kind.item_kind_of_inline        all(isinstance(v, dict))   ✓
            csv_converter.prune_arrays_of_dicts   all(isinstance(x, dict))   ✓
            _is_array_of_dicts                    isinstance(value[0], dict) ✗

        A sparse array — ``{"records": [{"id":1}, None, {"id":3}]}``, i.e. what an
        external API returns — passed the first-element test, went to
        ``dict_to_csv``, and ``_flatten(None)`` raised ``'NoneType' object has no
        attribute 'items'``: **the node's output could not be stored at all**, so
        the task failed. Reverse the order and it stored fine. This is the SAME
        shape the root-level fix addresses, and in production it always arrives as
        a FIELD, not as the root — so fixing only the root would have left the
        only reachable instance of it broken.

        Now a heterogeneous array stays inline as one JSON cell (lossless), which
        is what ``prune_arrays_of_dicts`` already did for the recursive path.
        """
        return (
            isinstance(value, list)
            and len(value) > 0
            and all(isinstance(x, dict) for x in value)
        )

    @staticmethod
    def _extract_columns(data: Any) -> list[str]:
        """Return column names from data."""
        if isinstance(data, dict):
            flat = CsvConverter._flatten(data)
            return list(flat.keys())
        if isinstance(data, list) and data and isinstance(data[0], dict):
            flat = CsvConverter._flatten(data[0])
            return list(flat.keys())
        return []

    # ``_SYNTHETIC_COLUMNS`` deliberately does NOT include ``__value``: it is a
    # reserved column, but it carries the LOGICAL value, so stripping it would
    # delete the data. It is removed by unwrapping (``_unwrap_value``), not by
    # filtering.
