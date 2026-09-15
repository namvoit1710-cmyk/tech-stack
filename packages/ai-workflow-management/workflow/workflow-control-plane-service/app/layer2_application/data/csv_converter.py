"""CSV converter domain service — pure logic, no I/O."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.value_objects.csv_document import ROW_ID_COLUMN
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    KINDS,
    NULL,
    OBJECT,
    SCALAR_KINDS,
    STRING,
    UNKNOWN,
    VALUE_COLUMN,
    empty_for_kind,
    item_kind_of_inline,
    uses_value_column,
)

# csv's default per-field size limit (131072 bytes) is easily exceeded by
# JSON-encoded lists/dicts stored in a single CSV cell. Raise it to a bounded
# ceiling — large enough for legitimate data, but still a guardrail against
# pathologically large/malformed input. Only affects reading; writing has no limit.
_CSV_FIELD_SIZE_LIMIT = 128 * 1024 * 1024  # 128 MB
csv.field_size_limit(_CSV_FIELD_SIZE_LIMIT)


class CsvConverter:
    """Converts form data to CSV bytes and back."""

    # ------------------------------------------------------------------
    # Flattening / un-flattening helpers for nested dicts
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        """Flatten a nested dict using dot-notation keys.

        Values that are dicts are recursively flattened.
        Lists and other non-dict values are JSON-encoded.
        """
        items: dict[str, Any] = {}
        for key, value in data.items():
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if is_file_ref(value):
                # SA-1905 F1 — a nested file_ref (e.g. a file-backed branch
                # output carried through a MERGE) is a POINTER, not tabular
                # data. Recursing into it shredded the pointer into
                # ``field.file_id`` / ``field.artifacts`` / … columns
                # (22-column explosion, lossy round-trip, and the child's
                # schema polluting the parent's column_types). Keep it whole in
                # one JSON cell so it round-trips as a file_ref and downstream
                # can resolve lazily through it.
                items[full_key] = json.dumps(value, default=str)
            elif isinstance(value, dict):
                items.update(CsvConverter._flatten(value, full_key))
            elif isinstance(value, (list, tuple)):
                items[full_key] = json.dumps(value, default=str)
            elif value is None:
                items[full_key] = ""
            else:
                items[full_key] = value
        return items

    @staticmethod
    def _resolve_collisions(flat: dict[str, Any]) -> tuple[set[str], set[str]]:
        """Resolve leaf/branch collisions at the same path — SA-1984.

        ``_flatten`` walks each row independently: a field that is ``None`` in
        one record and a dict in another emits a LEAF column ``x`` (from the
        null row) *and* BRANCH columns ``x.a`` (from the dict row). The CSV
        header is the union, so ``DictReader`` hands every row both, filling the
        side it never had with ``""``.

        The reader then let whichever arrived last win — which crashed when the
        leaf landed first (``None["a"] = …``) and silently destroyed the object
        when it landed second. Column order alone decided crash vs data loss.

        The row carries the answer, and it has two halves:

        * some ``x.*`` cell is non-empty ⇒ this row's ``x`` IS an object; its
          empty leaf cell is the other row's null bleeding through the shared
          header. Drop the leaf.
        * every ``x.*`` cell is empty ⇒ this row's ``x`` is the leaf (``None``,
          a scalar, a JSON list). Drop the branch, or ``x`` reads back as
          ``{"a": None}`` instead of ``None``.

        Returns ``(leaves_to_drop, branch_prefixes_to_drop)``. Only paths that
        appear BOTH ways are touched, so an ordinary nested object — one with no
        competing leaf column — is never affected.
        """
        keys = [k for k in flat if k is not None]
        keyset = set(keys)
        collided: set[str] = set()
        for key in keys:
            if "." not in key:
                continue
            parts = key.split(".")
            for depth in range(1, len(parts)):
                prefix = ".".join(parts[:depth])
                if prefix in keyset:
                    collided.add(prefix)

        drop_leaf: set[str] = set()
        drop_branch: set[str] = set()
        for prefix in collided:
            head = prefix + "."
            has_value = any(
                flat[k] not in (None, "")
                for k in keys
                if k.startswith(head)
            )
            (drop_leaf if has_value else drop_branch).add(prefix)
        return drop_leaf, drop_branch

    @staticmethod
    def _unflatten(
        flat: dict[str, Any],
        column_types: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Reconstruct a nested dict from dot-notation keys.

        When *column_types* is supplied (the write-time schema stored on the
        file ref), each leaf is cast by its declared type — deterministic and
        lossless. For columns absent from the map (or when no map is given —
        e.g. refs that predate the schema), fall back to the ``_try_parse_value``
        heuristic so old data still round-trips.

        SA-1984 — leaf/branch collisions at the same path are resolved from the
        row itself (see ``_object_prefixes``) rather than by column order. The
        fix is RETROACTIVE: files already at rest carry both columns, so an
        output that is unreadable today becomes readable with no migration.
        """
        result: dict[str, Any] = {}
        drop_leaf, drop_branch = CsvConverter._resolve_collisions(flat)
        for key, value in flat.items():
            # ``csv.DictReader`` collects a ragged row's overflow fields
            # (more values than headers) under ``restkey=None``. Those
            # values have no column name to unflatten to, so skip them
            # rather than crash on ``None.split('.')``.
            if key is None:
                continue
            # SA-1984 — this row proved ``key`` is an object; the children
            # below own the path, so its leaf cell belongs to another row.
            if key in drop_leaf:
                continue
            # SA-1984 — mirror: every child cell is empty, so the leaf is the
            # real value and these branch columns belong to another row.
            if drop_branch and any(
                key.startswith(prefix + ".") for prefix in drop_branch
            ):
                continue
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                nested = current.get(part)
                if not isinstance(nested, dict):
                    # A leaf already landed here. ``objects`` guarantees we only
                    # ever displace an EMPTY one, so nothing real is dropped.
                    nested = {}
                    current[part] = nested
                current = nested
            declared = column_types.get(key) if column_types else None
            if declared:
                parsed = CsvConverter._cast_value(
                    value, declared, keep_empty_string=(key == VALUE_COLUMN),
                )
            else:
                # No declared type → JSON/number/bool heuristic.
                parsed = CsvConverter._try_parse_value(value)
            current[parts[-1]] = parsed
        return result

    @staticmethod
    def _try_parse_value(value: Any) -> Any:
        """Attempt to parse a string as JSON, number, or boolean."""
        if not isinstance(value, str):
            return value
        if value == "":
            return None
        # Try JSON (for lists, dicts)
        if value.startswith(("[", "{")):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                pass
        # Boolean
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        # Number
        try:
            int_val = int(value)
            return int_val
        except ValueError:
            pass
        try:
            float_val = float(value)
            return float_val
        except ValueError:
            pass
        return value

    # ------------------------------------------------------------------
    # SA-1910 — recursive nested-array extraction / path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def prune_arrays_of_dicts(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list]]:
        """Split *row* into (row-without-array-of-dicts, {dotted_path: array}).

        Recurses into nested objects, so ``{"order": {"lines": [...]}}`` yields
        the array under ``"order.lines"`` and keeps ``order``'s scalars in the
        pruned row. Only arrays whose elements are ALL dicts are extracted;
        empty arrays and scalar/mixed arrays stay inline (JSON cell), so the
        split is lossless. A nested file_ref is left untouched (F1 handles it).
        """
        from app.layer1_domain.entities.data_reference import is_file_ref
        pruned: dict[str, Any] = {}
        arrays: dict[str, list] = {}

        def walk(src: dict[str, Any], dst: dict[str, Any], prefix: str) -> None:
            for k, v in src.items():
                path = k if not prefix else f"{prefix}.{k}"
                if isinstance(v, list) and len(v) > 0 and all(isinstance(x, dict) for x in v):
                    arrays[path] = v
                elif isinstance(v, dict) and not is_file_ref(v):
                    sub: dict[str, Any] = {}
                    walk(v, sub, path)
                    dst[k] = sub
                else:
                    dst[k] = v

        walk(row, pruned, "")
        return pruned, arrays

    @staticmethod
    def set_by_path(obj: dict[str, Any], dotted: str, value: Any) -> None:
        """Set ``obj[a][b][c] = value`` for ``dotted='a.b.c'``, creating
        intermediate dicts as needed (used to re-attach a split child array)."""
        parts = dotted.split(".")
        cur = obj
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value

    # ------------------------------------------------------------------
    # Schema-driven typing (write-time truth instead of read-time guessing)
    # ------------------------------------------------------------------

    @staticmethod
    def _cast_value(value: Any, type_str: str, keep_empty_string: bool = False) -> Any:
        """Cast a CSV cell (always a string on read) to its declared type.

        Unlike ``_try_parse_value``, a ``string`` column is returned verbatim —
        so ID-like data ("0123", "1.20", "007") is preserved instead of being
        coerced to a number. Unknown types fall back to the heuristic.

        ``keep_empty_string`` — an empty cell in a declared ``string`` column
        reads back as ``""`` instead of ``None``. It is OFF by default and set
        only for the reserved ``__value`` column (A4), for a reason worth writing
        down, because it is the same disease as SA-1984:

            **A missing field and a genuine empty string write the SAME empty
            cell.** ``[{"a": 1}, {"a": 1, "late": "hi"}]`` gives row 0 an empty
            ``late`` cell; so does ``[{"late": ""}]``. The bytes cannot tell them
            apart, so no reader can. Returning ``""`` everywhere would give every
            sparse row a phantom empty-string field — a far more common shape than
            a deliberate empty string — so the default stays ``None`` and SA-1988
            (B4) keeps its real problem: it is not a casting bug, it is a
            **format** that cannot represent the difference.

            ``__value`` is the one column where the empty cell is unambiguous, and
            that is *arranged*, not assumed. "Missing" cannot occur (a list of N
            values has N rows, each carrying exactly one), and a null cannot occur
            either — because ``value_cells_are_json`` forces a string list that
            contains a null onto the JSON-encoded path, where ``null`` and ``""``
            are written distinctly. So an empty cell in a ``string``-typed
            ``__value`` column IS an empty string.
        """
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
        return CsvConverter._try_parse_value(value)

    @staticmethod
    def _leaf_type(value: Any) -> str | None:
        """Map a Python leaf value to a CSV column type, or None to not lock it.

        ``bool`` is checked before ``int`` (bool is an int subclass). Lists/dicts
        are JSON-encoded into one cell by ``_flatten`` → ``"json"``. ``None`` is
        left unlocked so a later non-null row can decide the column's type.
        """
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, (list, tuple, dict)):
            return "json"
        if isinstance(value, str):
            return "string"
        return None

    @staticmethod
    def _infer_into(data: dict[str, Any], prefix: str, out: dict[str, str]) -> None:
        """Walk *data* the same way ``_flatten`` does, recording leaf types.

        First non-null observation per flat column wins (ponytail: assumes
        roughly homogeneous columns; a mixed-type column takes its first row's
        type — fine for tabular data, revisit only if heterogeneity bites)."""
        for key, value in data.items():
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if is_file_ref(value):
                # SA-1905 F1 — matches ``_flatten``: a nested file_ref is one
                # JSON cell, so its column type is ``json`` (do NOT recurse
                # into the pointer's fields).
                if full_key not in out:
                    out[full_key] = "json"
            elif isinstance(value, dict):
                CsvConverter._infer_into(value, full_key, out)
            elif full_key not in out:
                t = CsvConverter._leaf_type(value)
                if t is not None:
                    out[full_key] = t

    @staticmethod
    def infer_column_types(
        data: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, str]:
        """Derive a ``{flat_column: type}`` schema from the real Python data,
        captured at write time before CSV stringification loses the types."""
        out: dict[str, str] = {}
        if isinstance(data, dict):
            CsvConverter._infer_into(data, "", out)
        elif isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    CsvConverter._infer_into(row, "", out)
        return out

    # ------------------------------------------------------------------
    # Bidirectional conversion: dict ↔ CSV
    # ------------------------------------------------------------------

    @staticmethod
    def dict_to_csv(data: dict[str, Any] | list[dict[str, Any]]) -> tuple[bytes, str, int]:
        """Convert a dict or list of dicts to CSV bytes.

        Nested dicts are flattened with dot-notation keys.
        Returns ``(csv_bytes, filename, row_count)``.
        """
        if isinstance(data, list):
            if not data:
                return b"", "data.csv", 0
            rows = [CsvConverter._flatten(row) for row in data]
            # Union of all keys to handle heterogeneous rows
            all_keys: list[str] = []
            seen: set[str] = set()
            for row in rows:
                for k in row:
                    if k not in seen:
                        all_keys.append(k)
                        seen.add(k)
            if not all_keys:
                # Every row flattened to nothing — a list of EMPTY objects. A CSV
                # needs at least one column to have rows at all, so with no columns
                # ``DictWriter`` emitted blank lines and ``inject_row_ids`` then
                # dropped them: the file held ZERO rows while the ref declared
                # ``row_count = len(data)``. Harmless-looking, but the declaration
                # is now load-bearing — ``_items_total`` sizes a lazy LOOP from
                # ``row_count``, so the loop ran a phantom iteration with a null
                # item, and the resolver raised a false "the file download likely
                # failed" when it saw row_count=1 over empty content.
                #
                # Emit the row-id column explicitly, which is exactly what the Rust
                # producer does — so the two writers agree AND the elements survive.
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=[ROW_ID_COLUMN])
                writer.writeheader()
                for i in range(len(rows)):
                    writer.writerow({ROW_ID_COLUMN: i})
                return buf.getvalue().encode("utf-8"), "data.csv", len(rows)
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=all_keys)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: str(row.get(k, "")) if row.get(k) is not None else "" for k in all_keys})
            return buf.getvalue().encode("utf-8"), "data.csv", len(rows)

        # Single dict
        flat = CsvConverter._flatten(data)
        if not flat:
            return b"", "data.csv", 0
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(flat.keys()))
        writer.writeheader()
        writer.writerow({k: str(v) if v is not None else "" for k, v in flat.items()})
        return buf.getvalue().encode("utf-8"), "data.csv", 1

    # ------------------------------------------------------------------
    # SA-1977 A4 — scalar / list-of-scalar roots via the reserved __value column
    # ------------------------------------------------------------------

    @staticmethod
    def values_to_csv(values: list[Any]) -> tuple[bytes, str, int]:
        """Write a list of NON-dict values as one row each under ``__value``.

        The shapes CSV could not hold at all: ``_flatten`` requires a mapping, so
        ``[1,2,3]`` raised ``'int' object has no attribute 'items'`` and a bare
        ``"hello"`` raised the ``str`` equivalent — a large list of numbers or a
        text blob from a node was simply unstorable.

        One reserved column keeps them inside CSV, so nothing else has to change:
        still ``text/csv`` (no file-service processor to add), still N rows (so
        still OData-pageable and lazily row-readable), still a table a steward can
        edit — a one-column table is the natural editor for a list of values.

        Cells are written WITHOUT ``_flatten``: a structured element is JSON-
        encoded whole rather than recursed into dotted children, which is what
        keeps a heterogeneous list from emitting a leaf column and a branch
        column at the same path (the SA-1984 collision).
        """
        if not values:
            return b"", "data.csv", 0
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=[VALUE_COLUMN])
        writer.writeheader()
        json_cells = CsvConverter.value_cells_are_json(values)
        for v in values:
            writer.writerow({VALUE_COLUMN: CsvConverter._value_cell(v, json_cells)})
        return buf.getvalue().encode("utf-8"), "data.csv", len(values)

    @staticmethod
    def value_cells_are_json(values: list[Any]) -> bool:
        """True when every ``__value`` cell must be JSON-encoded.

        Two reasons, and they are the same reason: **an empty cell would be
        ambiguous otherwise.**

        * a **mixed** list — one declared column type has to cover every element,
          and a column whose type depends on which element came FIRST is the very
          bug this Story exists to remove;
        * a **string list containing a null** — ``None`` and ``""`` both write the
          empty cell, so ``["a", None, "b"]`` would read back as ``["a", "", "b"]``.
          JSON-encoding writes ``null`` and ``""`` distinctly.

        This is what makes ``_cast_value``'s ``keep_empty_string`` **true rather
        than merely convenient**: after this rule, a ``__value`` column declared
        ``string`` can no longer contain a null, so its empty cell is unambiguously
        an empty string. (For ``number``/``boolean`` an empty cell is a null, and
        an empty string is not a member of the type — no ambiguity either way.)
        """
        kind = item_kind_of_inline(values)
        if kind == UNKNOWN:
            return True
        return kind == STRING and any(v is None for v in values)

    @staticmethod
    def _value_cell(value: Any, json_cells: bool) -> str:
        """One ``__value`` cell — JSON-encoded whole, or in its plain CSV form."""
        if json_cells:
            return json.dumps(value, default=str)
        if value is None:
            return ""
        if isinstance(value, (list, tuple, dict)):
            return json.dumps(value, default=str)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def infer_value_column_types(values: list[Any]) -> dict[str, str]:
        """The declared type of the ``__value`` column for a scalar-root file.

        Must agree with :meth:`value_cells_are_json` — the cells and the declared
        type are one decision, made once.
        """
        if CsvConverter.value_cells_are_json(values):
            return {VALUE_COLUMN: "json"}
        kind = item_kind_of_inline(values)
        if kind == NULL:
            return {}  # all-null → leave unlocked, reads back as None
        if kind in (ARRAY, OBJECT):
            return {VALUE_COLUMN: "json"}
        return {VALUE_COLUMN: kind}  # string / number / boolean

    @staticmethod
    def _unwrap_value(row: Any) -> Any:
        """Unwrap one ``__value`` row back to the scalar it holds.

        **Only call this when the DECLARATION says the value is stored in the
        reserved column** (``value_kind.uses_value_column``). Deciding it from
        the row's own columns instead destroys an ordinary node output that
        happens to be a list of one-key dicts — see that function.

        Defensive, not inferential: if the declaration says ``__value`` but the
        row has no such column (a hand-edited file, a ref that outlived its
        file), return the row rather than raising.
        """
        if not isinstance(row, dict) or VALUE_COLUMN not in row:
            return row
        return row[VALUE_COLUMN]

    @staticmethod
    def csv_to_dict(
        csv_bytes: bytes,
        column_types: dict[str, str] | None = None,
        value_kind: str = "",
        item_kind: str = "",
    ) -> Any:
        """Parse CSV bytes back to the value that was written.

        Dot-notation column names are unflattened to nested dicts.

        SA-1977 — *value_kind* is the root type the WRITER declared. When present
        it decides the shape, full stop:

        * ``array``  → a list of every row (``[]`` for none, a 1-element list for
          one). *item_kind* says whether those rows are ``__value`` wrappers.
        * ``object`` → a dict, whatever the row count.
        * a scalar kind → the single ``__value`` cell.

        Without it (a ref written before this existed) fall back to the old
        row-count guess — *"1 row → dict, N rows → list"* — which is exactly why
        ``[{x}]`` used to read back as ``{x}`` and ``[]`` as ``{}``. The guess is
        now the LEGACY path, not the contract.
        """
        kind = value_kind if value_kind in KINDS else UNKNOWN
        if not csv_bytes:
            return empty_for_kind(kind)
        text = csv_bytes.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = [CsvConverter._unflatten(row, column_types) for row in reader]
        return CsvConverter.rows_to_value(rows, value_kind, item_kind)

    @staticmethod
    def rows_to_value(
        rows: list[dict[str, Any]],
        value_kind: str = "",
        item_kind: str = "",
    ) -> Any:
        """Rows → the value the writer stored. The tail of :meth:`csv_to_dict`,
        extracted so a second reader cannot answer this question differently.

        SA-1905: the Arrow sidecar produces the same ``list[dict]`` from a
        different encoding, and the root-kind rule must not be written twice.
        Two expressions of one rule, disagreeing, IS the defect ``value_kind``
        exists to remove — see the note on :func:`declared_item_kind` for what it
        cost the last time.

        Deliberately takes ROWS, not bytes: whether the bytes were CSV or Arrow
        is the caller's business and stops mattering here.
        """
        kind = value_kind if value_kind in KINDS else UNKNOWN
        unwrap = uses_value_column(kind, item_kind)

        if kind == ARRAY:
            return [CsvConverter._unwrap_value(r) for r in rows] if unwrap else rows
        if kind == OBJECT:
            return rows[0] if rows else {}
        if kind in SCALAR_KINDS:
            return CsvConverter._unwrap_value(rows[0]) if rows else None

        # Legacy ref (no declared kind): the historic row-count heuristic.
        if len(rows) == 0:
            return {}
        if len(rows) == 1:
            return rows[0]
        return rows

    # ------------------------------------------------------------------
    # Original methods (unchanged)
    # ------------------------------------------------------------------

    @staticmethod
    def single_row_to_csv(form_data: dict[str, Any]) -> bytes:
        """Convert a flat dict to CSV bytes: keys = headers, values = 1 row."""
        if not form_data:
            return b""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(form_data.keys()))
        writer.writeheader()
        writer.writerow({k: str(v) if v is not None else "" for k, v in form_data.items()})
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def multi_row_to_csv(rows: list[dict[str, Any]], columns: list[str] | None = None) -> bytes:
        """Convert a list of dicts to CSV bytes. Each dict = 1 row."""
        if not rows:
            return b""
        if columns is None:
            columns = list(rows[0].keys())
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(row.get(k, "")) if row.get(k) is not None else "" for k in columns})
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def determine_csv_input(
        resolved_input: dict[str, Any],
        locked_defaults: dict[str, Any],
        data_mode: str,
    ) -> tuple[bytes, str]:
        """Separate csv-eligible fields from locked_defaults, convert to CSV.

        Returns (csv_bytes, filename).
        Any key present in locked_defaults is excluded from CSV; everything else
        is form data.

        For csv_multi: looks for the first key whose value is list[dict] in the
        non-locked fields.
        """
        locked_keys = set(locked_defaults.keys())
        csv_fields = {k: v for k, v in resolved_input.items() if k not in locked_keys}

        if data_mode == "csv_single":
            csv_bytes = CsvConverter.single_row_to_csv(csv_fields)
            return csv_bytes, "form_data.csv"

        if data_mode == "csv_multi":
            # Look for the first key whose value is list[dict]
            for key, value in csv_fields.items():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    csv_bytes = CsvConverter.multi_row_to_csv(value)
                    return csv_bytes, f"{key}.csv"
            # Fallback: treat all csv_fields as a single row
            csv_bytes = CsvConverter.single_row_to_csv(csv_fields)
            return csv_bytes, "form_data.csv"

        return b"", "form_data.csv"
