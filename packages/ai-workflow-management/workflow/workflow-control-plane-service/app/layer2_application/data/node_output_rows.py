"""Descriptor ⇄ rows — the mapping ``AIW_RUN_NODE_OUTPUT`` is built around.

Design + every measurement: ``solace/specs/wcp-node-output-storage-redesign.md``.
Pure functions, no database: this is where the correctness lives, so it is
testable without one.

A ``file_ref`` is a recursive tree — a root carrying ``artifacts``, each of which
may carry its own. The table stores it as **adjacency**: one row per node, each
pointing at its parent's ``ARTIFACT_SEQ``. Two measurements forced that over a
materialised path:

* ``artifact_id`` is **relative** and **collides** — 55 distinct ids across 2,688
  children on tenant-1, because ``add_artifact`` defaults it to the child's own
  dotted segment. Keying on it merges different artifacts.
* a path string grows with nesting depth, so "unlimited nesting" eventually
  overflows whatever width it is given. Adjacency keeps every column fixed-width
  and puts depth in ROWS. Observed depth already reaches 6.

The row-level linkage (``__row_id`` / ``__parent_row_id``) is NOT here on
purpose: it lives inside the CSV files and stays there untouched. Only the
artifact-level tree moves into rows.
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.data_reference import (
    FILE_REF_MARKER,
    ROOT_ARTIFACT_ID,
    is_file_ref,
    schema_hash,
)

#: Keys a root descriptor owns that an artifact does not, and vice versa.
#: ``flatten`` records which side a row came from via ``DEPTH == 0`` so
#: ``rebuild`` can emit exactly the dict the producer emitted — an artifact that
#: grows a ``columns`` key it never had is a silent shape change.
_ROOT_ONLY = ("columns", "row_count", "value_kind", "item_kind", "version", "etag")


def flatten(file_ref: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict]]:
    """Walk a recursive ``file_ref`` into ``(rows, schemas)``.

    ``rows`` are dicts keyed by ``AIW_RUN_NODE_OUTPUT`` column name, minus the
    run/node identity the caller supplies. ``schemas`` maps ``schema_hash`` →
    ``{"columns": [...], "column_types": {...}}`` for ``AIW_SCHEMA_DEF``, already
    deduplicated within this descriptor.

    ``ARTIFACT_SEQ`` is assigned in depth-first order with the root at 0, so a
    child's ``PARENT_SEQ`` always refers to a row emitted before it — which is
    what lets ``rebuild`` work in one pass.
    """
    rows: list[dict[str, Any]] = []
    schemas: dict[str, dict] = {}
    if not is_file_ref(file_ref):
        return rows, schemas

    def emit(node: dict[str, Any], parent_seq: int | None, depth: int,
             ordinal: int) -> None:
        seq = len(rows)
        # Artifacts carry ``columns`` too — verified against live tenant-1 rows,
        # where all four children of a real ``get_comments`` output had one.
        # ``add_artifact`` does not set the key, which is what made an earlier
        # version hash artifacts on ``column_types`` alone and drop their
        # columns on the way back. That loses the CHILD table's header, which is
        # exactly the input ``get_variable_suggestions_use_case.py:522`` turns
        # into an empty dropdown without raising.
        columns = node.get("columns")
        column_types = node.get("column_types") or {}
        digest = schema_hash(columns, column_types)
        if digest and digest not in schemas:
            schemas[digest] = {
                "columns": list(columns or []),
                "column_types": dict(column_types),
            }
        rows.append({
            "ARTIFACT_SEQ": seq,
            "PARENT_SEQ": parent_seq,
            "DEPTH": depth,
            "ORDINAL": ordinal,
            "ARTIFACT_PATH": str(node.get("artifact_id") or ROOT_ARTIFACT_ID),
            # NOT decoration: _reconstruct_recursive re-attaches a child with
            # set_by_path(row, art["name"], …) and register_artifacts keys
            # {{$artifacts.<name>}} off it. artifact_id is not a substitute.
            "ARTIFACT_NAME": str(node.get("name") or ""),
            "ARTIFACT_TYPE": str(node.get("artifact_type") or ""),
            "PARENT_ROW_KEY_COLUMN": node.get("parent_row_key_column"),
            "FILE_ID": str(node.get("file_id") or ""),
            "CONTENT_TYPE": str(node.get("content_type") or "text/csv"),
            "VALUE_KIND": str(node.get("value_kind") or ""),
            "ITEM_KIND": str(node.get("item_kind") or ""),
            "ROW_COUNT": int(node.get("row_count") or 0),
            "PAYLOAD_BYTES": int(node.get("size_bytes") or 0),
            "SCHEMA_HASH": digest,
            "REF_VERSION": int(node.get("version") or 0),
            "ETAG": str(node.get("etag") or ""),
            # add_artifact stamps created_at on every artifact (defaulting to
            # now), so it is part of the value the producer wrote. Dropping it
            # would make the round-trip lossy for every nested descriptor.
            "CREATED_AT": str(node.get("created_at") or ""),
        })
        for i, child in enumerate(node.get("artifacts") or []):
            if isinstance(child, dict):
                emit(child, seq, depth + 1, i)

    emit(file_ref, None, 0, 0)
    return rows, schemas


def rebuild(rows: list[dict[str, Any]], schemas: dict[str, dict]) -> dict[str, Any] | None:
    """Inverse of :func:`flatten` — rows back into the recursive ``file_ref``.

    Returns ``None`` when there is no root row. Children are re-attached in
    ``ORDINAL`` order so the artifacts list comes back in the order it was
    written; ``_reconstruct_recursive`` reads them positionally.
    """
    if not rows:
        return None
    by_seq: dict[int, dict[str, Any]] = {}
    children: dict[int, list[tuple[int, int]]] = {}
    root_seq: int | None = None

    for row in rows:
        seq = int(row["ARTIFACT_SEQ"])
        by_seq[seq] = row
        parent = row.get("PARENT_SEQ")
        if parent is None:
            root_seq = seq
        else:
            children.setdefault(int(parent), []).append((int(row.get("ORDINAL") or 0), seq))
    if root_seq is None:
        return None

    def build(seq: int) -> dict[str, Any]:
        row = by_seq[seq]
        schema = schemas.get(row.get("SCHEMA_HASH") or "", {})
        is_root = int(row.get("DEPTH") or 0) == 0
        node: dict[str, Any]
        if is_root:
            node = {
                FILE_REF_MARKER: True,
                "artifact_id": row.get("ARTIFACT_PATH") or ROOT_ARTIFACT_ID,
                "file_id": row.get("FILE_ID") or "",
                "content_type": row.get("CONTENT_TYPE") or "text/csv",
                "row_count": int(row.get("ROW_COUNT") or 0),
                "columns": list(schema.get("columns") or []),
                "column_types": dict(schema.get("column_types") or {}),
                # The dictionary key, carried on the descriptor by build_file_ref
                # itself. Recomputing it here instead of echoing the stored value
                # would hide a drift between writer and reader.
                "schema_hash": row.get("SCHEMA_HASH") or "",
                "value_kind": row.get("VALUE_KIND") or "",
                "item_kind": row.get("ITEM_KIND") or "",
                "size_bytes": int(row.get("PAYLOAD_BYTES") or 0),
                "artifacts": [],
                "version": int(row.get("REF_VERSION") or 0),
                "etag": row.get("ETAG") or "",
            }
        else:
            node = {
                "artifact_id": row.get("ARTIFACT_PATH") or "",
                "file_id": row.get("FILE_ID") or "",
                "name": row.get("ARTIFACT_NAME") or "",
                "content_type": row.get("CONTENT_TYPE") or "text/csv",
                "size_bytes": int(row.get("PAYLOAD_BYTES") or 0),
                "artifact_type": row.get("ARTIFACT_TYPE") or "",
                "parent_row_key_column": row.get("PARENT_ROW_KEY_COLUMN"),
                "column_types": dict(schema.get("column_types") or {}),
                "schema_hash": row.get("SCHEMA_HASH") or "",
                "created_at": row.get("CREATED_AT") or "",
                "artifacts": [],
            }
            # Only when the stored schema actually carried columns. An artifact
            # built by ``add_artifact`` alone has no such key, and inventing an
            # empty one would be a shape change; the recursive-split path does
            # set it, and dropping THAT loses the child table's header. The one
            # case these two cannot be told apart is an artifact whose
            # ``columns`` was explicitly ``[]`` — which carries no information,
            # since every reader does ``.get("columns") or []``.
            if schema.get("columns"):
                node["columns"] = list(schema["columns"])
            for key, col in (("row_count", "ROW_COUNT"), ("value_kind", "VALUE_KIND"),
                             ("item_kind", "ITEM_KIND")):
                # _store_rows_recursive stamps these onto nested_array children
                # after add_artifact returns. Absent ⇒ the producer did not set
                # them, so do not invent them.
                val = row.get(col)
                if val not in (None, "", 0):
                    node[key] = int(val) if key == "row_count" else val
        for _ordinal, child_seq in sorted(children.get(seq, [])):
            node["artifacts"].append(build(child_seq))
        return node

    return build(root_seq)
