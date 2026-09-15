"""``AutoFormSchema`` — artifact / scalar-column discovery over an auto-form schema.

Detection is driven by ``outputType`` (the FE↔BE semantic contract), not
``fieldWrapper`` (a presentation label):

- ``outputType == "array"`` with a ``key`` → file-backed artifact. The row shape
  is the array's first child; the array's CSV columns are that row's scalar
  children. Nested arrays inside a row's cells become depth-bumped dotted
  artifacts (e.g. ``orders.line_items``).
- ``outputType == "object"`` → passthrough container; recurse into ``fields``.
  Scalars at any object depth land in the ``__root__`` CSV as dotted-path columns
  (``tabs.tab_1.created_at``) — object containers never mint their own artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from collections.abc import Iterator

_SCALAR_OUTPUT_TYPES = frozenset({"string", "number", "boolean"})


@dataclass(frozen=True)
class SchemaArtifact:
    artifact_id: str          # dotted path, e.g. "customer.orders.line_items"
    name: str                  # leaf key, e.g. "line_items"
    columns: list[str]         # scalar-cell keys, in order
    depth: int                 # 0 = root artifact, >0 = nested
    # Author-declared default rows for this array artifact — each a dict keyed by
    # column name. Used by the provisioner to seed the empty CSV with starter
    # rows. Empty list = header-only.
    default_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AutoFormSchema:
    """An auto-form ``input_schema`` + its artifact / scalar-column queries."""

    input_schema: Any

    def artifacts(self) -> list[SchemaArtifact]:
        """Flat list of artifact descriptors discovered in the schema."""
        if not isinstance(self.input_schema, list):
            return []
        out: list[SchemaArtifact] = []
        for fld in self.input_schema:
            self._walk(fld, parent_path=(), depth=0, out=out)
        return out

    def iter_artifacts(self) -> Iterator[SchemaArtifact]:
        """Generator variant of :meth:`artifacts`."""
        yield from self.artifacts()

    def scalar_defaults(self) -> dict[str, Any]:
        """``{column: default}`` for scalar fields that declare a ``default`` —
        same dotted-path naming as :meth:`scalar_columns`. Seeds the root CSV's
        single starter data row."""
        out: dict[str, Any] = {}
        for path, fld in self._iter_scalar_fields(self.input_schema):
            if "default" in fld:
                out[path] = fld["default"]
        return out

    def scalar_columns(self) -> list[str]:
        """Ordered column names of every scalar field reachable through object
        containers. Top-level scalars keep their flat key; scalars under keyed
        object containers get the dotted path prefix (``tabs.tab_1.created_at``).
        Scalars inside arrays are skipped (they're table-CSV columns)."""
        return [path for path, _ in self._iter_scalar_fields(self.input_schema)]

    # ── internal recursion ────────────────────────────────────────────

    @classmethod
    def _walk(
        cls,
        fld: Any,
        *,
        parent_path: tuple[str, ...],
        depth: int,
        out: list[SchemaArtifact],
    ) -> None:
        if not isinstance(fld, dict):
            return
        key = fld.get("key") or ""
        output_type = fld.get("outputType") or ""
        children = fld.get("fields") or []

        if output_type == "array" and key:
            path = parent_path + (key,)
            artifact_id = ".".join(path)
            row_cells = cls._row_cells(children)
            columns = [c["key"] for c in row_cells if c.get("key")
                       and (c.get("outputType") or "") in _SCALAR_OUTPUT_TYPES]
            raw_default = fld.get("default")
            default_rows: list[dict[str, Any]] = []
            if isinstance(raw_default, list):
                default_rows = [r for r in raw_default if isinstance(r, dict)]
            out.append(SchemaArtifact(
                artifact_id=artifact_id,
                name=key,
                columns=columns,
                depth=depth,
                default_rows=default_rows,
            ))
            # Recurse into each row-cell to find nested arrays (deeper tables).
            for cell in row_cells:
                cls._walk(cell, parent_path=path, depth=depth + 1, out=out)
            return

        # Container (object / unknown): recurse into ``fields``. The parent_path
        # only picks up this key for keyed object containers so dotted ids stay
        # readable. Scalars under object containers are NOT artifacts.
        new_path = parent_path + (key,) if key and output_type == "object" else parent_path
        for child in children:
            cls._walk(child, parent_path=new_path, depth=depth, out=out)

    @staticmethod
    def _row_cells(array_children: list[Any]) -> list[dict[str, Any]]:
        """Return the cell fields of the array's row object — the first
        object-typed child's ``fields`` (wrapper names don't matter)."""
        if not array_children:
            return []
        row = array_children[0]
        if not isinstance(row, dict):
            return []
        if (row.get("outputType") or "object") not in ("object", ""):
            return []
        cells = row.get("fields") or []
        return [c for c in cells if isinstance(c, dict)]

    @classmethod
    def _iter_scalar_fields(
        cls, input_schema: Any, prefix: tuple[str, ...] = (),
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield ``(dotted_column, field)`` for scalars at any object depth.
        Keyed object containers contribute a path segment; arrays stop recursion."""
        if not isinstance(input_schema, list):
            return
        for fld in input_schema:
            if not isinstance(fld, dict):
                continue
            key = fld.get("key") or ""
            output_type = fld.get("outputType") or ""
            if output_type in _SCALAR_OUTPUT_TYPES:
                if key:
                    yield ".".join(prefix + (key,)), fld
                continue
            if output_type == "array":
                continue
            new_prefix = prefix + (key,) if key and output_type == "object" else prefix
            yield from cls._iter_scalar_fields(fld.get("fields") or [], new_prefix)
