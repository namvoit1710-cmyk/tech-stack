import copy
import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from worker_sdk.layer1_domain.exceptions import FileRefResolutionError
from worker_sdk.layer1_domain.value_objects.file_reference import (
    is_file_ref,
    is_file_id_input,
)

_log = logging.getLogger("WorkerSDK")

_API_PREFIX = "/api/v1"

# SA-2072 — synthetic columns the CSV/artifact split adds for linking parent↔child
# rows. Kept DURING reconstruction (grouping keys) and stripped once, deeply, at
# the end — mirrors ``DataStoreService._SYNTHETIC_COLUMNS`` + the OData ``_row_index``.
_SYNTHETIC_COLUMNS = frozenset({"__row_id", "__parent_row_id", "_row_index"})
# The reserved scalar/list-of-scalar column. A claim-checked TASK input is an
# object whose split children are object-arrays, so ``__value`` never appears on
# this path; named only to keep the cast rule identical to the control plane.
_VALUE_COLUMN = "__value"

# The root kinds stored IN that column. A per-layer copy of the control plane's
# ``value_kind.SCALAR_KINDS`` — same decision as the ``kind`` vocabulary (Q1):
# copied, not imported, and pinned by a test rather than by an import.
#
# Before this existed ``_materialize_ref`` knew only object/array, so a scalar
# ref fell into the legacy row-count guess, was called an object, and came back
# as ``{"__value": "..."}`` — the SA-1977 one-key-dict defect, on the worker side
# of the wire. Nothing hit it while only the control plane could write scalar
# refs; the worker-side writer (large-string streaming) is what makes it
# reachable, so the two must never ship apart.
_SCALAR_KINDS = frozenset({"string", "number", "boolean", "null"})

# Page size for fetching a file-backed collection (SA-1905). A single unbounded
# GET returns the ENTIRE collection in one response — for a large streamed
# collection that is a multi-hundred-MB JSON body that overruns the client
# timeout / buffer and fails the task. Fetching in bounded pages keeps each
# response small so large collections actually resolve. (Peak worker RAM still
# equals the full list for a handler that needs it — true per-row streaming is a
# per-handler follow-up, e.g. the mapping-worker Iterate node.)
_FETCH_PAGE_SIZE = 10000
# Hard safety cap so a misbehaving file service (always-full pages) can't loop
# forever / grow unbounded. 5M rows is far above any real collection.
_FETCH_MAX_ROWS = 5_000_000

# Rows per window for the streaming read. Peak RAM is a function of THIS, not of
# the payload: one window's root rows plus their joined children. 500 keeps a
# window in the single-digit MB for the widest tree seen live (117 columns), while
# staying large enough that the per-window child reads are not the dominant cost.
_DEFAULT_WINDOW_ROWS = 500


def _unwrap(data: Any) -> Any:
    """Unwrap response envelope from ResponseWrapperMiddleware."""
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


class StreamedCollection:
    """A file-backed array that has NOT been materialised.

    Iterating it yields the root objects, holding at most one window. Handed to a
    worker that has opted in (``stream_collections``) INSTEAD of the list, so the
    collection never becomes one big Python object on the way to becoming bytes
    again.

    It deliberately does **not** pretend to be a list: no ``__len__``, no
    ``__getitem__``, and ``json.dumps`` raises. A caller that has not been taught
    to stream must break loudly rather than silently serialise something else.
    Re-iterable, because a worker may need to size it and then send it.
    """

    __slots__ = ("_resolver", "_ref", "_window_size")

    def __init__(self, resolver: "HttpFileRefResolver", ref: dict[str, Any], window_size: int) -> None:
        self._resolver = resolver
        self._ref = ref
        self._window_size = window_size

    @property
    def ref(self) -> dict[str, Any]:
        return self._ref

    def __iter__(self) -> Iterator[Any]:
        return self._resolver.iter_objects(self._ref, self._window_size)

    def __repr__(self) -> str:
        return (
            f"StreamedCollection(file_id={self._ref.get('file_id')!r}, "
            f"row_count={self._ref.get('row_count')!r}, window={self._window_size})"
        )


class HttpFileRefResolver:
    """Resolve ``__file_ref`` objects and ``file_id`` inputs via the file service."""

    def __init__(
        self, file_service_url: str, max_input_bytes: int = 0,
        stream_collections: bool = False,
        window_size: int = _DEFAULT_WINDOW_ROWS,
    ) -> None:
        self._base_url = file_service_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)
        # SA-1943 — cap the cumulative wire bytes a single file-backed input
        # collection may pull into worker RAM (0 = disabled). Guards the small
        # worker pod against OOM on a large streamed collection. A negative /
        # misconfigured value clamps to 0 (disabled) rather than rejecting every
        # input (``fetched > -1`` would always be true).
        self._max_input_bytes = max(0, int(max_input_bytes or 0))
        # Opt-in. A worker that has not asked for handles keeps getting lists, so
        # the other nine are byte-for-byte unaffected.
        self._stream_collections = bool(stream_collections)
        self._window_size = max(1, int(window_size or _DEFAULT_WINDOW_ROWS))
        _log.debug(
            "HttpFileRefResolver initialised, base_url=%s, max_input_bytes=%d",
            self._base_url, self._max_input_bytes,
        )

    def resolve_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Resolve file references in *inputs*.

        Handles three patterns:
        1. **Top-level file_id input** — the entire inputs dict is
           ``{"file_id": "...", "data_mode": "csv_single", ...}``.
           The CSV data is fetched and the first row becomes the inputs dict.
           Extra keys (locked_defaults etc.) are merged on top.
        2. **Nested __file_ref objects** — individual values within the inputs
           dict that have ``{"__file_ref": true, "file_id": "..."}``.
        3. **Reconstructable file_ref (SA-2072)** — a full ``__file_ref`` that
           carries an artifact tree (``nested_array`` / ``array_field``) and/or a
           declared ``value_kind``. The control plane emits this when it
           claim-checks a large TASK input; the object is rebuilt from the whole
           tree here (worker-side mirror of ``DataStoreService.materialize``),
           instead of fetching one scalar CSV and silently dropping its array
           fields. Checked BEFORE pattern 1 (a claim-checked ref may carry a
           ``data_mode:"csv_single"`` fallback for older workers) and again inside
           ``_walk``, so a reconstructable ref is rebuilt whether it is the whole
           input or nested inside it.
        """
        resolved = copy.deepcopy(inputs)

        # Pattern 3 (SA-2072) — a reconstructable full file_ref (it carries an
        # artifact tree and/or a declared value_kind) is rebuilt from its WHOLE
        # tree, and MUST be checked BEFORE the csv_single fast-path. The control
        # plane tags a claim-checked large input with a ``data_mode:"csv_single"``
        # fallback so an OLDER worker still reads it (as the one scalar CSV, i.e.
        # today's behaviour). Here that fallback must NOT win — the artifacts make
        # the ref reconstructable, so rebuild it in full instead of losing them.
        if self._is_reconstructable_ref(resolved):
            return self._materialize_ref(resolved, budget=[0])

        # Pattern 1: entire inputs is a file_id reference
        if is_file_id_input(resolved):
            file_id = resolved["file_id"]
            data_mode = resolved.get("data_mode", "csv_single")
            _log.debug("Resolving top-level file_id input: file_id=%s, data_mode=%s", file_id, data_mode)

            rows = self._fetch_file_data(file_id)

            if data_mode == "csv_single":
                # Single-row CSV: first row becomes the inputs dict
                if rows:
                    row = dict(rows[0])
                    row.pop("_row_index", None)
                    resolved = row
                else:
                    resolved = {}
            else:
                # csv_multi: keep as list, remove _row_index
                resolved = {
                    "data": [
                        {k: v for k, v in row.items() if k != "_row_index"}
                        for row in rows
                    ]
                }

            # Merge extra keys from the original inputs (locked_defaults, etc.)
            # but skip file_id and data_mode themselves
            for k, v in inputs.items():
                if k not in ("file_id", "data_mode") and k not in resolved:
                    resolved[k] = v

        # Pattern 2: walk for nested __file_ref objects
        return self._walk(resolved)

    def _walk(self, obj: Any) -> Any:
        if is_file_ref(obj):
            # SA-2072 — a ref that carries an artifact tree and/or a declared
            # ``value_kind`` is reconstructed from the WHOLE tree (parity with the
            # control plane). A bare, artifact-less, kind-less ref keeps the legacy
            # behaviour (fetch the one file's rows) so existing callers are byte-
            # identical — the change is additive, scoped to the previously-broken
            # (or brand-new full-ref) shape.
            if self._is_reconstructable_ref(obj):
                # Opt-in streaming: hand back a HANDLE for an array root instead
                # of materialising it, so the collection never becomes one big
                # Python object. Only arrays — one object is one object, and a
                # handle there buys nothing while complicating every caller.
                if self._stream_collections and obj.get("value_kind") == "array":
                    return StreamedCollection(self, obj, self._window_size)
                return self._materialize_ref(obj, budget=[0])
            file_id = obj.get("file_id", "")
            _log.debug("Resolving nested file_ref: file_id=%s", file_id)
            return self._fetch_file_data(file_id)
        if isinstance(obj, dict):
            return {k: self._walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._walk(item) for item in obj]
        return obj

    # ------------------------------------------------------------------
    # SA-2072 — full file_ref reconstruction (worker-side mirror of
    # ``DataStoreService.materialize`` / ``_reconstruct_recursive``)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_reconstructable_ref(ref: Any) -> bool:
        """True when a file_ref must be rebuilt from its tree rather than fetched
        as flat rows: it carries child artifacts, or it declares a root
        ``value_kind`` (object/array) that fixes its shape."""
        return is_file_ref(ref) and (
            bool(ref.get("artifacts"))
            or ref.get("value_kind") in ("object", "array")
            or ref.get("value_kind") in _SCALAR_KINDS
        )

    def _materialize_ref(self, ref: dict[str, Any], budget: list | None = None) -> Any:
        """Reconstruct the original nested value from *ref* + its artifact tree.

        Fetches the root rows, recursively re-joins each ``nested_array`` child by
        ``__parent_row_id`` → the parent's ``__row_id``, re-attaches each
        ``array_field`` child whole, honours the declared ``value_kind`` (object →
        the single row, array → the list), then strips synthetic columns deeply.
        Byte-equivalent to ``DataStoreService.materialize`` for the shapes the
        claim-check produces. ``budget`` (``[used_bytes]``) accounts the whole
        tree's downloads against the SA-1943 input cap cumulatively."""
        rows = self._reconstruct_ref_rows(ref, budget)
        if not isinstance(rows, list):
            # Verbatim JSON payload (non-CSV file) — return as stored.
            return self._strip_synthetic_deep(rows)
        kind = ref.get("value_kind")
        if kind in _SCALAR_KINDS:
            # Mirrors ``CsvConverter.rows_to_value``: the value is the reserved
            # column of the single row, and NO rows means None — not ``{}``,
            # which is the empty of an object, not of a scalar.
            #
            # Decided from the DECLARATION, never from the columns. Sniffing
            # "the only column is __value, so unwrap it" would destroy an
            # ordinary list of one-key dicts, which is the trap
            # ``uses_value_column`` documents on the control-plane side.
            if not rows:
                return None
            row = self._strip_synthetic_deep(rows[0])
            return row.get(_VALUE_COLUMN) if isinstance(row, dict) else row
        if kind not in ("object", "array"):
            # Legacy ref with no declared kind: the historic row-count guess.
            kind = "object" if len(rows) == 1 else "array"
        rows = [self._strip_synthetic_deep(r) for r in rows]
        if kind == "array":
            return rows
        return rows[0] if rows else {}

    def _reconstruct_ref_rows(
        self, ref: dict[str, Any], budget: list | None = None,
    ) -> Any:
        """Return *ref*'s rows as nested dicts, synthetic columns KEPT for linking.

        Mirror of ``DataStoreService._reconstruct_recursive`` over OData: fetch +
        unflatten the main rows, then for each child artifact recurse and graft it
        onto the parent rows. The caller honours ``value_kind`` and strips the
        synthetic columns."""
        file_id = ref.get("file_id", "")
        raw = self._fetch_file_data(file_id, budget) if file_id else []
        if not isinstance(raw, list):
            return raw  # verbatim JSON payload — no rows to unflatten
        column_types = ref.get("column_types") or {}
        rows = [self._unflatten(r, column_types) for r in raw]
        # An only-arrays object root has no scalar main row; synthesize row 0 so
        # its children (``__parent_row_id`` 0) still attach.
        if not rows and ref.get("value_kind") == "object":
            rows = [{"__row_id": 0}]

        for art in ref.get("artifacts", []) or []:
            name = art.get("name", "")
            if not art.get("file_id") or not name:
                continue
            atype = art.get("artifact_type")
            if atype == "nested_array":
                child = self._reconstruct_ref_rows(art, budget)
                child_list = child if isinstance(child, list) else ([child] if child else [])
                groups: dict[Any, list] = {}
                for c in child_list:
                    if isinstance(c, dict):
                        groups.setdefault(self._to_int(c.get("__parent_row_id")), []).append(c)
                for prow in rows:
                    rid = self._to_int(prow.get("__row_id"))
                    if rid in groups:
                        self._set_by_path(prow, name, groups[rid])
            elif atype == "array_field":
                # Flat split: one top-level array field of a single-object root.
                child = self._reconstruct_ref_rows(art, budget)
                child_list = child if isinstance(child, list) else ([child] if child else [])
                for prow in rows:
                    self._set_by_path(prow, name, child_list)

        return rows

    # ------------------------------------------------------------------
    # Windowed read — peak RAM is a function of the WINDOW, not the payload
    # ------------------------------------------------------------------

    def iter_objects(
        self, ref: dict[str, Any], window_size: int = _DEFAULT_WINDOW_ROWS,
        budget: list | None = None,
    ) -> Iterator[Any]:
        """Yield the root objects one at a time, holding at most one window.

        Byte-for-byte what ``resolve_inputs`` returns for the same ref — that
        parity IS the contract, and it is what the windowed-join tests pin. The
        difference is only how much is resident at once.
        """
        kind = ref.get("value_kind")
        if kind not in ("object", "array"):
            # A legacy ref with no declared kind: the whole-read decides the root
            # type from the TOTAL row count, which a windowed read cannot know
            # without reading everything anyway. Fall back rather than invent a
            # second rule that disagrees.
            out = self._materialize_ref(ref, budget)
            yield from (out if isinstance(out, list) else [out])
            return
        if kind == "object":
            window = next(iter(self.iter_root_windows(ref, window_size, budget)), [])
            yield self._strip_synthetic_deep(window[0]) if window else {}
            return
        for window in self.iter_root_windows(ref, window_size, budget):
            for row in window:
                yield self._strip_synthetic_deep(row)

    def iter_root_windows(
        self, ref: dict[str, Any], window_size: int = _DEFAULT_WINDOW_ROWS,
        budget: list | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield the root's rows in windows, each fully joined with its children.

        Synthetic columns are KEPT, exactly as ``_reconstruct_ref_rows`` leaves
        them, so the two readers can be compared directly.
        """
        file_id = ref.get("file_id", "")
        if not file_id:
            return
        window_size = max(1, int(window_size))
        column_types = ref.get("column_types") or {}
        # THE BUDGET IS PER WINDOW when no tree budget was handed in.
        #
        # ``RESOLVE_MAX_INPUT_BYTES`` exists to stop a whole collection landing in
        # a small pod's RAM. A windowed read never lands it, so accumulating
        # across windows would refuse payloads for a danger that no longer
        # applies — the cap would go on deciding what can be sent long after it
        # stopped describing what is resident. Reset per window and the guard
        # keeps its real job: one absurd window still refuses.
        #
        # An EXPLICIT budget from a caller is honoured as-is (that caller is
        # accounting a whole tree and means it).
        tree_budget = budget
        # An ``array_field`` child is the SAME list on every parent row, so it
        # cannot be scoped by parent — read it once and reuse it across windows.
        # It is the flat split of a single-object root, so it is small by
        # construction; re-reading it per window would be the expensive mistake.
        array_fields = self._array_field_children(
            ref, tree_budget if tree_budget is not None else [0],
        )
        skip = 0
        while True:
            page_budget = tree_budget if tree_budget is not None else [0]
            raw = self._fetch_page(
                file_id, page_budget,
                {"$top": str(window_size), "$skip": str(skip)},
            )
            if not isinstance(raw, list):
                return  # verbatim JSON payload — not a windowable collection
            rows = [self._unflatten(r, column_types) for r in raw]
            if not rows:
                # An only-arrays object root has no scalar main row; synthesize
                # row 0 so its children (``__parent_row_id`` 0) still attach.
                if skip == 0 and ref.get("value_kind") == "object":
                    rows = [{"__row_id": 0}]
                else:
                    return
            self._attach_children_windowed(rows, ref, page_budget)
            for name, child_list in array_fields.items():
                for prow in rows:
                    self._set_by_path(prow, name, child_list)
            yield rows
            if len(raw) < window_size:
                return  # short page = end of data
            skip += window_size

    def _attach_children_windowed(
        self, rows: list[dict[str, Any]], ref: dict[str, Any], budget: list | None,
    ) -> None:
        """Join this window's ``nested_array`` children on — same rules as
        ``_reconstruct_ref_rows``, including that a parent with NO children gets
        no field at all (not an empty list; that would be a different document).

        Children are fetched with a server-side ``$filter`` scoped to this
        window's ``__row_id`` range, so a window costs one small read per
        artifact instead of the whole child table. Grandchildren recurse on the
        CHILD rows' own id range, because a grandchild's ``__parent_row_id``
        refers to the child's ``__row_id``, not the root's.
        """
        ids = [self._to_int(r.get("__row_id")) for r in rows]
        ids = [i for i in ids if i is not None]
        if not ids:
            return
        lo, hi = min(ids), max(ids)
        for art in ref.get("artifacts") or []:
            name = art.get("name", "")
            if not art.get("file_id") or not name:
                continue
            if art.get("artifact_type") != "nested_array":
                continue  # array_field is attached once, by the caller
            child_raw = self._fetch_rows_filtered(art["file_id"], lo, hi, budget)
            child_types = art.get("column_types") or {}
            child_rows = [self._unflatten(r, child_types) for r in child_raw]
            if art.get("artifacts"):
                self._attach_children_windowed(child_rows, art, budget)
            groups: dict[Any, list] = {}
            for c in child_rows:
                if isinstance(c, dict):
                    groups.setdefault(self._to_int(c.get("__parent_row_id")), []).append(c)
            for prow in rows:
                rid = self._to_int(prow.get("__row_id"))
                if rid in groups:
                    self._set_by_path(prow, name, groups[rid])

    def _fetch_rows_filtered(
        self, file_id: str, lo: int, hi: int, budget: list | None,
    ) -> list[dict[str, Any]]:
        """Every row whose ``__parent_row_id`` falls in ``[lo, hi]``, paged.

        The range is inclusive of both ends, expressed as ``ge lo and lt hi+1``.

        This docstring used to say that form was "verified to actually filter
        server-side". It was not. What was verified live was the SINGLE-clause form
        (``ge 5000`` starts at 5000, ``eq 4321`` returns one row); the CONJUNCTION was
        assumed, and the file service dropped its first clause — ``QueryAndFilter.where``
        was a dict keyed by column, so the later predicate replaced the earlier one and
        every window read from row 0. Correctness held (the join groups by
        ``__parent_row_id``) but each window paid the whole payload's bytes, and window 9
        of a 5 000-row tree fetched 67 897 568 B against a 67 108 864 B cap.

        The service now keeps every predicate on a column. ``_assert_window_honoured``
        is what stops that regressing back into an invisible cost.
        """
        flt = f"__parent_row_id ge {lo} and __parent_row_id lt {hi + 1}"
        out: list[dict[str, Any]] = []
        skip = 0
        while True:
            rows = self._fetch_page(
                file_id, budget,
                {"$top": str(_FETCH_PAGE_SIZE), "$skip": str(skip), "$filter": flt},
            )
            if not isinstance(rows, list):
                return out
            self._assert_window_honoured(file_id, rows, lo, hi)
            out.extend(rows)
            if len(rows) < _FETCH_PAGE_SIZE:
                return out
            skip += _FETCH_PAGE_SIZE

    @staticmethod
    def _assert_window_honoured(
        file_id: str, rows: list[dict[str, Any]], lo: int, hi: int,
    ) -> None:
        """Refuse rows this window did not ask for.

        A ``$filter`` the service does not honour comes back as MORE rows with HTTP
        200, which no caller can tell apart from a genuinely wide window — the failure
        mode is silence, so the check has to be here rather than in a log line.

        Deliberately NOT a monotonicity check as well: the join groups children by
        ``__parent_row_id``, so row ORDER cannot change the result, and an assertion
        that guards nothing is one more thing to break.

        Ship order this enforces: the FILE SERVICE fix goes out first. Against an
        unfixed service every windowed read fails here, loudly and on the first page,
        instead of quietly spending a payload's bytes on one window.
        """
        for row in rows:
            raw = row.get("__parent_row_id")
            if raw in (None, ""):
                continue                      # unlinked row; the join ignores it too
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue                      # not a row id; not this guard's business
            if lo <= pid <= hi:
                continue
            raise FileRefResolutionError(
                f"file service did not honour the window $filter on {file_id}: asked "
                f"for __parent_row_id in [{lo}, {hi}], got {pid}. Refusing to read "
                f"outside the window — an ignored $filter is how a windowed read "
                f"silently costs a whole payload instead of one window."
            )

    def _array_field_children(
        self, ref: dict[str, Any], budget: list | None,
    ) -> dict[str, list]:
        """Read every ``array_field`` child whole, once. See the note in
        ``iter_root_windows`` for why these cannot be windowed."""
        out: dict[str, list] = {}
        for art in ref.get("artifacts") or []:
            if art.get("artifact_type") != "array_field":
                continue
            name = art.get("name", "")
            if not art.get("file_id") or not name:
                continue
            child = self._reconstruct_ref_rows(art, budget)
            out[name] = child if isinstance(child, list) else ([child] if child else [])
        return out

    # -- CSV cell / path helpers (mirror of CsvConverter, for parity) ----------

    @staticmethod
    def _try_parse_value(value: Any) -> Any:
        """Parse a CSV string as JSON / number / boolean (no declared type)."""
        if not isinstance(value, str):
            return value
        if value == "":
            return None
        if value.startswith(("[", "{")):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                pass
        low = value.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    @staticmethod
    def _cast_value(value: Any, type_str: str, keep_empty_string: bool = False) -> Any:
        """Cast a CSV cell to its declared type (mirror of CsvConverter._cast_value)."""
        if not isinstance(value, str):
            return value
        if value == "":
            if keep_empty_string and type_str == "string":
                return ""
            return None
        if type_str == "string":
            return value
        if type_str == "boolean":
            low = value.lower()
            if low == "true":
                return True
            if low == "false":
                return False
            return value
        if type_str == "number":
            try:
                return int(value)
            except ValueError:
                pass
            try:
                return float(value)
            except ValueError:
                return value
        if type_str == "json":
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value
        return HttpFileRefResolver._try_parse_value(value)

    @classmethod
    def _unflatten(
        cls, flat: dict[str, Any], column_types: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Reconstruct a nested dict from dot-notation keys, casting each leaf by
        its declared type (mirror of CsvConverter._unflatten). ``_row_index`` (an
        OData pagination artifact) is dropped up front, exactly as the CP does."""
        result: dict[str, Any] = {}
        for key, value in flat.items():
            if key is None or key == "_row_index":
                continue
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                nxt = current.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    current[part] = nxt
                current = nxt
            declared = column_types.get(key) if column_types else None
            if declared:
                current[parts[-1]] = cls._cast_value(
                    value, declared, keep_empty_string=(key == _VALUE_COLUMN),
                )
            else:
                current[parts[-1]] = cls._try_parse_value(value)
        return result

    @staticmethod
    def _set_by_path(obj: dict[str, Any], dotted: str, value: Any) -> None:
        """Set ``obj[a][b][c] = value`` for ``dotted='a.b.c'`` (mirror of
        CsvConverter.set_by_path), used to re-attach a split child array."""
        parts = dotted.split(".")
        cur = obj
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value

    @staticmethod
    def _to_int(v: Any) -> Any:
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    @classmethod
    def _strip_synthetic_deep(cls, obj: Any) -> Any:
        """Recursively drop the synthetic linking columns at every level."""
        if isinstance(obj, dict):
            return {
                k: cls._strip_synthetic_deep(v)
                for k, v in obj.items()
                if k not in _SYNTHETIC_COLUMNS
            }
        if isinstance(obj, list):
            return [cls._strip_synthetic_deep(x) for x in obj]
        return obj

    def _fetch_file_data(
        self, file_id: str, budget: list | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch a file-backed collection in bounded pages via OData
        ``$top``/``$skip``. Accumulates until a short page (end of data), the
        safety cap, or a non-list body. Each HTTP response stays small so a
        large collection resolves instead of timing out on one huge response.

        ``budget`` (``[used_bytes]``, SA-2072/C4): when a reconstruction fetches
        the root plus many child artifacts, the SA-1943 input cap must apply
        CUMULATIVELY across the whole tree, not per file. Seed the running total
        from it and write each page back, so one huge tree still raises instead of
        slipping under the cap file-by-file. ``None`` keeps the per-call behaviour
        (the legacy single-file paths)."""
        all_rows: list[dict[str, Any]] = []
        skip = 0
        # The running total must survive across PAGES even when no tree budget was
        # handed in — otherwise a collection that only exceeds the cap cumulatively
        # slips through page by page. (Caught by
        # ``test_cumulative_over_two_pages_raises`` when this was first extracted.)
        page_budget = budget if budget is not None else [0]
        while True:
            rows = self._fetch_page(
                file_id, page_budget,
                {"$top": str(_FETCH_PAGE_SIZE), "$skip": str(skip)},
            )
            if not isinstance(rows, list):
                # Non-list body (JSON verbatim payload) — return as-is; paging
                # doesn't apply. Only possible on the first page.
                return rows if skip == 0 else all_rows
            all_rows.extend(rows)
            if len(rows) < _FETCH_PAGE_SIZE:
                break
            skip += _FETCH_PAGE_SIZE
            if skip >= _FETCH_MAX_ROWS:
                # SA-1943 — a many-small-rows collection can hit the row cap
                # before the byte cap; raise LOUDLY rather than silently
                # truncating (silent truncation = undetectable data loss).
                raise FileRefResolutionError(
                    f"file-backed input {file_id} exceeds the {_FETCH_MAX_ROWS}-row "
                    f"fetch cap — refusing to silently truncate. Consume it in "
                    f"bounded windows / an Iterate handler, or lift the cap."
                )
        return all_rows

    def _fetch_page(
        self, file_id: str, budget: list | None, params: dict[str, str],
    ) -> Any:
        """ONE OData page, with the SA-1943 byte accounting applied.

        Extracted so the whole-collection read and the windowed read share one
        implementation of the budget. Two copies of that accounting would drift,
        and the copy that drifts is the one that stops guarding.
        """
        url = f"{self._base_url}{_API_PREFIX}/odata/{file_id}/data"
        _log.debug("Fetching file data from %s (%s)", url, params)
        fetched_bytes = budget[0] if budget is not None else 0
        try:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            # SA-1943 — account the wire bytes of THIS page BEFORE json-parsing
            # + accumulating, so a large collection raises instead of OOMing
            # the (small) worker pod. ``response.content`` is the raw bytes
            # already read by httpx; the parsed list is larger still.
            fetched_bytes += len(response.content)
            if budget is not None:
                budget[0] = fetched_bytes  # persist the running total across the tree
            if self._max_input_bytes and fetched_bytes > self._max_input_bytes:
                raise FileRefResolutionError(
                    f"file-backed input {file_id} exceeds the worker input cap "
                    f"({self._max_input_bytes} bytes, ~{fetched_bytes} fetched) — "
                    f"refusing to load the whole collection into worker RAM "
                    f"(SA-1943 OOM guard). Consume it in bounded windows / an "
                    f"Iterate handler, or raise RESOLVE_MAX_INPUT_BYTES."
                )
            body = _unwrap(response.json())
        except FileRefResolutionError:
            raise
        except httpx.HTTPStatusError as exc:
            raise FileRefResolutionError(
                f"File service returned {exc.response.status_code} for file {file_id}: {exc}"
            ) from exc
        except Exception as exc:
            raise FileRefResolutionError(
                f"Failed to resolve file_ref for file {file_id}: {exc}"
            ) from exc
        # The OData response has {"file_id", "total_matches", "data": [...]}
        return body["data"] if isinstance(body, dict) and "data" in body else body
        return all_rows

    def close(self) -> None:
        self._client.close()
