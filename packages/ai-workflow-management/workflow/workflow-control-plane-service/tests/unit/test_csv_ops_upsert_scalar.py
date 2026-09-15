"""Unit: ``csv_ops.upsert_scalar_row`` / ``list_columns`` schema auto-expand.

The object-artifact upsert must accept keys outside the current schema (the FE
can send a form field that wasn't in the initial ``input_schema``) and hold them
as new columns — unlike ``insert_rows`` (drops unknown keys) and
``update_rows_by_ids`` (raises). Existing/provisioned-but-unfilled columns and
the synthetic ``__row_id`` must survive.
"""
from __future__ import annotations

from app.layer1_domain.value_objects.csv_document import CsvDocument


def test_fresh_csv_creates_single_row_with_row_id():
    out, row_id = CsvDocument(b"").upsert_scalar_row( {"a": "1", "b": "2"})
    assert row_id == "1"
    assert CsvDocument(out).list_columns() == ["__row_id", "a", "b"]
    assert out.decode() == "__row_id,a,b\r\n1,1,2\r\n"


def test_provisioned_header_no_rows_is_preserved():
    # Provisioned root: header declares input+input_1, no data row yet.
    content = b"__row_id,input,input_1\r\n"
    out, row_id = CsvDocument(content).upsert_scalar_row( {"input": "hello"})
    # input_1 must NOT be dropped just because this submit omitted it.
    assert CsvDocument(out).list_columns() == ["__row_id", "input", "input_1"]
    assert row_id == "1"
    text = out.decode()
    assert "hello" in text


def test_new_key_is_appended_as_column():
    content = b"__row_id,input\r\n"
    out, _ = CsvDocument(content).upsert_scalar_row( {"input": "x", "newcol": "y"})
    cols = CsvDocument(out).list_columns()
    assert cols == ["__row_id", "input", "newcol"]
    assert "y" in out.decode()


def test_existing_row_is_updated_and_row_id_kept():
    content = b"__row_id,input\r\n7,old\r\n"
    out, row_id = CsvDocument(content).upsert_scalar_row( {"input": "new", "extra": "z"})
    assert row_id == "7"  # existing __row_id preserved
    assert CsvDocument(out).list_columns() == ["__row_id", "input", "extra"]
    rows = out.decode().splitlines()
    assert rows[1].startswith("7,new")
    assert rows[1].endswith(",z")


def test_list_columns_empty():
    assert CsvDocument(b"").list_columns() == []
