"""Unit tests for csv_ops pure mutations."""

import csv
import io

import pytest

from app.layer1_domain.value_objects.csv_document import CsvDocument, ROW_ID_COLUMN, PARENT_ROW_ID_COLUMN


def _parse(content: bytes):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    return list(reader.fieldnames or []), [dict(r) for r in reader]


def _csv(header: list[str], rows: list[dict]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in header})
    return out.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# inject_row_ids
# ---------------------------------------------------------------------------

def test_inject_row_ids_backfills_missing_values():
    content = _csv(["sku", "qty"], [{"sku": "A", "qty": "1"}, {"sku": "B", "qty": "2"}])
    result = CsvDocument(content).inject_row_ids()
    header, rows = _parse(result)
    assert header[0] == ROW_ID_COLUMN
    assert len(rows) == 2
    assert all(r[ROW_ID_COLUMN] for r in rows)
    assert rows[0][ROW_ID_COLUMN] != rows[1][ROW_ID_COLUMN]


def test_inject_row_ids_preserves_existing_ids():
    content = _csv([ROW_ID_COLUMN, "sku"], [
        {ROW_ID_COLUMN: "r_kept", "sku": "A"},
    ])
    result = CsvDocument(content).inject_row_ids()
    _, rows = _parse(result)
    assert rows[0][ROW_ID_COLUMN] == "r_kept"


def test_inject_row_ids_appends_new_rows():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    result = CsvDocument(content).inject_row_ids( rows_needing_id=[{"sku": "B"}])
    _, rows = _parse(result)
    assert len(rows) == 2
    assert rows[1]["sku"] == "B"
    assert rows[1][ROW_ID_COLUMN] and rows[1][ROW_ID_COLUMN] != "r1"


# ---------------------------------------------------------------------------
# list_row_ids
# ---------------------------------------------------------------------------

def test_list_row_ids_preserves_order():
    content = _csv([ROW_ID_COLUMN, "sku"], [
        {ROW_ID_COLUMN: "r1", "sku": "A"},
        {ROW_ID_COLUMN: "r2", "sku": "B"},
    ])
    assert CsvDocument(content).list_row_ids() == ["r1", "r2"]


def test_list_row_ids_empty_content():
    assert CsvDocument(b"").list_row_ids() == []


# ---------------------------------------------------------------------------
# update_cell_by_row_id
# ---------------------------------------------------------------------------

def test_update_cell_by_row_id_updates_matching_row():
    content = _csv([ROW_ID_COLUMN, "sku", "qty"], [
        {ROW_ID_COLUMN: "r1", "sku": "A", "qty": "1"},
        {ROW_ID_COLUMN: "r2", "sku": "B", "qty": "2"},
    ])
    result = CsvDocument(content).update_cell_by_row_id( row_id="r2", column="qty", value=99)
    _, rows = _parse(result)
    assert rows[0]["qty"] == "1"
    assert rows[1]["qty"] == "99"


def test_update_cell_by_row_id_unknown_column_raises():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    with pytest.raises(ValueError, match="not in header"):
        CsvDocument(content).update_cell_by_row_id( row_id="r1", column="qty", value=5)


def test_update_cell_by_row_id_missing_row_silent_noop():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    result = CsvDocument(content).update_cell_by_row_id( row_id="r_missing", column="sku", value="Z")
    _, rows = _parse(result)
    assert rows[0]["sku"] == "A"


# ---------------------------------------------------------------------------
# update_rows_by_ids (bulk)
# ---------------------------------------------------------------------------

def test_update_rows_by_ids_bulk_apply():
    content = _csv([ROW_ID_COLUMN, "sku", "status"], [
        {ROW_ID_COLUMN: "r1", "sku": "A", "status": "new"},
        {ROW_ID_COLUMN: "r2", "sku": "B", "status": "new"},
        {ROW_ID_COLUMN: "r3", "sku": "C", "status": "new"},
    ])
    result, affected = CsvDocument(content).update_rows_by_ids( row_ids=["r1", "r3"], assignments={"status": "review"},
    )
    _, rows = _parse(result)
    assert affected == 2
    assert rows[0]["status"] == "review"
    assert rows[1]["status"] == "new"
    assert rows[2]["status"] == "review"


def test_update_rows_by_ids_empty_id_list_zero_affected():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    _, affected = CsvDocument(content).update_rows_by_ids( row_ids=[], assignments={"sku": "Z"})
    assert affected == 0


def test_update_rows_by_ids_unknown_column_raises():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    with pytest.raises(ValueError):
        CsvDocument(content).update_rows_by_ids( row_ids=["r1"], assignments={"missing": "x"})


# ---------------------------------------------------------------------------
# insert_rows
# ---------------------------------------------------------------------------

def test_insert_rows_appends_with_new_ids():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    result, ids = CsvDocument(content).insert_rows( [{"sku": "B"}, {"sku": "C"}])
    _, rows = _parse(result)
    assert len(rows) == 3
    assert len(ids) == 2
    assert all(ids)
    assert rows[1]["sku"] == "B"
    assert rows[2]["sku"] == "C"


def test_insert_rows_sets_parent_row_id_for_nested_artifact():
    content = _csv(
        [ROW_ID_COLUMN, PARENT_ROW_ID_COLUMN, "part_no"],
        [{ROW_ID_COLUMN: "r_li1", PARENT_ROW_ID_COLUMN: "r_parent", "part_no": "X"}],
    )
    result, ids = CsvDocument(content).insert_rows( [{"part_no": "Y"}], parent_row_id="r_parent",
    )
    _, rows = _parse(result)
    assert rows[1][PARENT_ROW_ID_COLUMN] == "r_parent"
    assert rows[1]["part_no"] == "Y"
    assert len(ids) == 1


def test_insert_rows_into_empty_artifact_creates_header():
    content = b""
    result, ids = CsvDocument(content).insert_rows( [{"sku": "A", "qty": "1"}])
    header, rows = _parse(result)
    assert ROW_ID_COLUMN in header
    assert "sku" in header and "qty" in header
    assert len(rows) == 1
    assert rows[0]["sku"] == "A"


def test_insert_rows_drops_unknown_columns_silently():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    result, _ = CsvDocument(content).insert_rows( [{"sku": "B", "unknown": "x"}])
    header, rows = _parse(result)
    assert "unknown" not in header
    assert rows[1]["sku"] == "B"


# ---------------------------------------------------------------------------
# delete_rows_by_ids
# ---------------------------------------------------------------------------

def test_delete_rows_by_ids_removes_matching():
    content = _csv([ROW_ID_COLUMN, "sku"], [
        {ROW_ID_COLUMN: "r1", "sku": "A"},
        {ROW_ID_COLUMN: "r2", "sku": "B"},
        {ROW_ID_COLUMN: "r3", "sku": "C"},
    ])
    result, removed = CsvDocument(content).delete_rows_by_ids( row_ids=["r1", "r3"])
    _, rows = _parse(result)
    assert removed == 2
    assert [r["sku"] for r in rows] == ["B"]


def test_delete_rows_by_ids_missing_ids_silent_noop():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    result, removed = CsvDocument(content).delete_rows_by_ids( row_ids=["r_missing"])
    _, rows = _parse(result)
    assert removed == 0
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# add_column
# ---------------------------------------------------------------------------

def test_add_column_backfills_default_value():
    content = _csv([ROW_ID_COLUMN, "sku"], [
        {ROW_ID_COLUMN: "r1", "sku": "A"},
        {ROW_ID_COLUMN: "r2", "sku": "B"},
    ])
    result = CsvDocument(content).add_column( column="flagged", default_value=False)
    header, rows = _parse(result)
    assert header[-1] == "flagged"
    assert all(r["flagged"] == "False" for r in rows)


def test_add_column_default_none_writes_empty():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    result = CsvDocument(content).add_column( column="note")
    _, rows = _parse(result)
    assert rows[0]["note"] == ""


def test_add_column_duplicate_raises():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    with pytest.raises(ValueError, match="already exists"):
        CsvDocument(content).add_column( column="sku")


# ---------------------------------------------------------------------------
# drop_column
# ---------------------------------------------------------------------------

def test_drop_column_removes_from_header_and_rows():
    content = _csv([ROW_ID_COLUMN, "sku", "qty"], [
        {ROW_ID_COLUMN: "r1", "sku": "A", "qty": "1"},
    ])
    result = CsvDocument(content).drop_column( "qty")
    header, rows = _parse(result)
    assert "qty" not in header
    assert "qty" not in rows[0]


def test_drop_column_unknown_raises():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    with pytest.raises(ValueError, match="not in header"):
        CsvDocument(content).drop_column( "missing")


# ---------------------------------------------------------------------------
# rename_column
# ---------------------------------------------------------------------------

def test_rename_column_updates_header_and_rows():
    content = _csv([ROW_ID_COLUMN, "old_name"], [{ROW_ID_COLUMN: "r1", "old_name": "A"}])
    result = CsvDocument(content).rename_column( old_name="old_name", new_name="new_name")
    header, rows = _parse(result)
    assert "old_name" not in header
    assert "new_name" in header
    assert rows[0]["new_name"] == "A"


def test_rename_column_to_same_name_is_noop():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    result = CsvDocument(content).rename_column( old_name="sku", new_name="sku")
    header, rows = _parse(result)
    assert "sku" in header
    assert rows[0]["sku"] == "A"


def test_rename_column_collision_raises():
    content = _csv([ROW_ID_COLUMN, "a", "b"], [{ROW_ID_COLUMN: "r1", "a": "A", "b": "B"}])
    with pytest.raises(ValueError, match="would collide"):
        CsvDocument(content).rename_column( old_name="a", new_name="b")


def test_rename_column_missing_raises():
    content = _csv([ROW_ID_COLUMN, "sku"], [{ROW_ID_COLUMN: "r1", "sku": "A"}])
    with pytest.raises(ValueError, match="not in header"):
        CsvDocument(content).rename_column( old_name="missing", new_name="new")


# ---------------------------------------------------------------------------
# merge_scalars
# ---------------------------------------------------------------------------

def test_merge_scalars_overwrites_existing_columns():
    content = _csv(
        [ROW_ID_COLUMN, "name", "total"],
        [{ROW_ID_COLUMN: "r1", "name": "Acme", "total": "100"}],
    )
    result = CsvDocument(content).merge_scalars( {"name": "Acme Corp"})
    _, rows = _parse(result)
    assert rows[0]["name"] == "Acme Corp"
    assert rows[0]["total"] == "100"


def test_merge_scalars_adds_new_columns():
    content = _csv([ROW_ID_COLUMN, "name"], [{ROW_ID_COLUMN: "r1", "name": "A"}])
    result = CsvDocument(content).merge_scalars( {"status": "active"})
    header, rows = _parse(result)
    assert "status" in header
    assert rows[0]["status"] == "active"


def test_merge_scalars_empty_content_creates_single_row_csv():
    content = b""
    result = CsvDocument(content).merge_scalars( {"name": "Acme"})
    header, rows = _parse(result)
    assert ROW_ID_COLUMN in header
    assert "name" in header
    assert len(rows) == 1
    assert rows[0]["name"] == "Acme"


# ---------------------------------------------------------------------------
# JSON serialization for nested values (nested forms + inline arrays)
# ---------------------------------------------------------------------------

import json as _json


def test_merge_scalars_json_encodes_dict_value():
    """A nested-form scalar (dict) must be JSON-encoded in the CSV cell, not
    emitted as Python repr."""
    content = b""
    result = CsvDocument(content).merge_scalars( {"address": {"line1": "123", "city": "NY"}})
    _, rows = _parse(result)
    cell = rows[0]["address"]
    assert _json.loads(cell) == {"line1": "123", "city": "NY"}


def test_merge_scalars_json_encodes_list_value():
    """An inline-array scalar (list) must be JSON-encoded."""
    content = b""
    result = CsvDocument(content).merge_scalars( {"tags": ["urgent", "finance"]})
    _, rows = _parse(result)
    cell = rows[0]["tags"]
    assert _json.loads(cell) == ["urgent", "finance"]


def test_merge_scalars_json_encodes_file_metadata_object():
    """File-metadata objects from UploadControl serialize as JSON."""
    content = b""
    meta = {
        "file_id": "f_abc", "filename": "x.pdf",
        "content_type": "application/pdf", "size_bytes": 1234,
    }
    result = CsvDocument(content).merge_scalars( {"evidence": meta})
    _, rows = _parse(result)
    assert _json.loads(rows[0]["evidence"]) == meta


def test_merge_scalars_passes_primitives_untouched():
    """str / int / bool unchanged — no JSON wrapping."""
    content = b""
    result = CsvDocument(content).merge_scalars( {"name": "Acme", "count": 5, "active": True},
    )
    _, rows = _parse(result)
    assert rows[0]["name"] == "Acme"
    assert rows[0]["count"] == "5"
    assert rows[0]["active"] == "True"


def test_insert_rows_json_encodes_nested_values_in_cells():
    """Rows with dict/list cell values (e.g., a column holding JSON config)
    route through the same _write() and serialize consistently."""
    content = _csv([ROW_ID_COLUMN, "name", "config"], [])
    result, _ = CsvDocument(content).insert_rows( [{"name": "A", "config": {"k": 1, "m": [1, 2]}}])
    _, rows = _parse(result)
    assert _json.loads(rows[0]["config"]) == {"k": 1, "m": [1, 2]}


def test_update_rows_by_ids_json_encodes_nested_assignments():
    content = _csv([ROW_ID_COLUMN, "name", "meta"], [
        {ROW_ID_COLUMN: "r1", "name": "A", "meta": "{}"},
    ])
    result, _ = CsvDocument(content).update_rows_by_ids( row_ids=["r1"], assignments={"meta": {"status": "ready"}},
    )
    _, rows = _parse(result)
    assert _json.loads(rows[0]["meta"]) == {"status": "ready"}
