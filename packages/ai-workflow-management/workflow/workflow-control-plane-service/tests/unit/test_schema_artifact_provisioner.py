"""Unit tests for schema_artifact_provisioner.provision_schema_artifacts."""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import (
    ROOT_ARTIFACT_ID,
    add_artifact,
    build_file_ref,
    find_artifact,
    is_file_ref,
)
from app.layer2_application.data.schema_artifact_provisioner import (
    NESTED_PARENT_ROW_KEY,
    provision_schema_artifacts,
)
from app.layer4_frameworks.clients.mock_file_service_client import (
    MockFileServiceClient,
)


def _table(key: str, columns: list[str], nested: list[dict] | None = None) -> dict:
    cells = [
        {"key": c, "outputType": "string",
         "fieldConfig": {"fieldWrapper": "TableCellWrapper"}}
        for c in columns
    ]
    if nested:
        cells.extend(nested)
    return {
        "key": key, "outputType": "array",
        "fieldConfig": {"fieldWrapper": "TableWrapper"},
        "fields": [{
            "key": "0", "outputType": "object",
            "fieldConfig": {"fieldWrapper": "TableRowWrapper"},
            "fields": cells,
        }],
    }


@pytest.mark.asyncio
async def test_no_artifacts_returns_existing_ref_unchanged():
    fs = MockFileServiceClient()
    schema = []
    existing = build_file_ref(file_id="keep_me")
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=existing,
        file_client=fs, node_id="n1",
    )
    assert result is existing


@pytest.mark.asyncio
async def test_force_root_mints_header_only_root_when_no_artifacts():
    """``force_root`` (used by INPUT) mints a header-only ``__row_id`` root
    even when the schema declares no scalars and no tables, so the node still
    has a non-empty ``__root__.file_id``."""
    fs = MockFileServiceClient()
    result = await provision_schema_artifacts(
        input_schema=[], existing_ref=None,
        file_client=fs, node_id="n1", force_root=True,
    )
    assert is_file_ref(result)
    assert result["file_id"]  # non-empty root
    assert result["artifact_type"] == "object_field"
    assert result["columns"] == ["__row_id"]
    assert not result["artifacts"]


@pytest.mark.asyncio
async def test_force_root_preserves_existing_root_file_id():
    """``force_root`` is a no-op when the root already carries a file_id
    (re-dispatch): the existing id is preserved, not re-minted."""
    fs = MockFileServiceClient()
    existing = build_file_ref(file_id="keep_me")
    result = await provision_schema_artifacts(
        input_schema=[], existing_ref=existing,
        file_client=fs, node_id="n1", force_root=True,
    )
    assert is_file_ref(result)
    assert result["file_id"] == "keep_me"


@pytest.mark.asyncio
async def test_creates_root_csv_for_top_level_scalars():
    """Top-level scalar fields produce a root ``object_field`` CSV so every
    node with schema-declared scalars is fully file-backed."""
    fs = MockFileServiceClient()
    schema = [
        {"key": "input", "outputType": "string", "fieldConfig": {}},
        {"key": "input_1", "outputType": "string", "fieldConfig": {}},
    ]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    assert is_file_ref(result)
    assert result["file_id"]  # root itself gets a file_id
    assert result["artifact_type"] == "object_field"
    assert result["content_type"] == "text/csv"
    assert result["columns"] == ["__row_id", "input", "input_1"]
    # No child artifacts — scalars are columns, not sub-files.
    assert not result["artifacts"]
    body = await fs.download_file(result["file_id"])
    assert body is not None
    assert body.decode("utf-8").strip().split(",") == ["__row_id", "input", "input_1"]


@pytest.mark.asyncio
async def test_root_csv_and_child_tables_provisioned_together():
    fs = MockFileServiceClient()
    schema = [
        {"key": "input", "outputType": "string", "fieldConfig": {}},
        _table("orders", ["sku", "qty"]),
    ]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    assert result["file_id"]
    assert result["artifact_type"] == "object_field"
    assert result["columns"] == ["__row_id", "input"]
    art = find_artifact(result, "orders")
    assert art is not None
    assert art["file_id"]
    assert art["artifact_type"] == "array_field"


@pytest.mark.asyncio
async def test_creates_empty_csv_for_root_table_when_no_existing_ref():
    fs = MockFileServiceClient()
    schema = [_table("orders", ["sku", "qty"])]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    assert is_file_ref(result)
    art = find_artifact(result, "orders")
    assert art is not None
    assert art["file_id"]  # non-empty
    # Empty CSV body: just the header line (with __row_id prepended).
    body = await fs.download_file(art["file_id"])
    assert body is not None
    assert body.decode("utf-8").strip().split(",") == ["__row_id", "sku", "qty"]
    # Array-only schema still provisions a __root__ CSV for uniformity.
    # Block-level columns/preview-rows on the read side need it to be
    # non-empty. The CSV is header-only with just __row_id.
    assert result["file_id"]
    assert result["columns"] == ["__row_id"]
    assert result["artifact_type"] == "object_field"
    assert result["content_type"] == "text/csv"
    root_body = await fs.download_file(result["file_id"])
    assert root_body is not None
    assert root_body.decode("utf-8").strip() == "__row_id"


@pytest.mark.asyncio
async def test_nested_table_gets_parent_row_key_and_dotted_id():
    fs = MockFileServiceClient()
    schema = [_table("orders", ["sku"], nested=[_table("line_items", ["price"])])]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    nested = find_artifact(result, "orders.line_items")
    assert nested is not None
    assert nested["parent_row_key_column"] == NESTED_PARENT_ROW_KEY
    assert nested["artifact_type"] == "nested_array_field"
    # Parent row key is injected into columns.
    assert NESTED_PARENT_ROW_KEY in nested["columns"]
    assert "price" in nested["columns"]


@pytest.mark.asyncio
async def test_merge_preserves_existing_artifact_file_ids():
    """Q3: merge-and-fill-gaps. Existing artifact keeps its file_id; only
    missing siblings/children are provisioned."""
    fs = MockFileServiceClient()
    existing = build_file_ref(file_id="root_existing")
    add_artifact(
        existing, file_id="orders_existing", name="orders",
        artifact_id="orders", artifact_type="array_field",
    )
    schema = [
        _table("orders", ["sku"]),
        _table("audit_log", ["event"]),  # missing — should be provisioned
    ]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=existing,
        file_client=fs, node_id="n1",
    )
    # Pre-existing artifact is untouched.
    assert find_artifact(result, "orders")["file_id"] == "orders_existing"
    # Missing sibling was provisioned.
    provisioned = find_artifact(result, "audit_log")
    assert provisioned is not None
    assert provisioned["file_id"]  # non-empty


@pytest.mark.asyncio
async def test_original_existing_ref_is_not_mutated():
    fs = MockFileServiceClient()
    existing = build_file_ref(file_id="root")
    schema = [_table("orders", ["sku"])]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=existing,
        file_client=fs, node_id="n1",
    )
    assert result is not existing
    assert find_artifact(existing, "orders") is None  # original untouched


@pytest.mark.asyncio
async def test_root_artifact_id_preserved():
    fs = MockFileServiceClient()
    schema = [_table("orders", ["sku"])]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    assert result["artifact_id"] == ROOT_ARTIFACT_ID


def _read_csv(body: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Parse uploaded CSV bytes into ``(header, rows)`` for assertions."""
    import csv
    import io
    text = body.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), [dict(r) for r in reader]


@pytest.mark.asyncio
async def test_array_default_seeds_artifact_csv():
    """Top-level array with a ``default`` list seeds the provisioned CSV with
    one row per default entry, each with a fresh ``__row_id``."""
    fs = MockFileServiceClient()
    table = _table("tblProcess", ["stepId", "description"])
    table["default"] = [
        {"stepId": "1", "description": "first"},
        {"stepId": "2", "description": "second"},
    ]
    schema = [table]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    art = find_artifact(result, "tblProcess")
    assert art is not None and art["file_id"]
    body = await fs.download_file(art["file_id"])
    header, rows = _read_csv(body)
    assert header == ["__row_id", "stepId", "description"]
    assert len(rows) == 2
    # Row ids are non-empty and unique.
    assert all(r["__row_id"] for r in rows)
    assert len({r["__row_id"] for r in rows}) == 2
    # Cells round-trip in declared column order.
    assert [r["stepId"] for r in rows] == ["1", "2"]
    assert [r["description"] for r in rows] == ["first", "second"]


@pytest.mark.asyncio
async def test_array_default_drops_unknown_columns():
    """Cells outside the schema-declared columns are dropped (DictWriter
    extrasaction='ignore')."""
    fs = MockFileServiceClient()
    table = _table("orders", ["sku"])
    table["default"] = [{"sku": "A1", "stray": "ignored"}]
    schema = [table]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    art = find_artifact(result, "orders")
    body = await fs.download_file(art["file_id"])
    header, rows = _read_csv(body)
    assert header == ["__row_id", "sku"]
    assert rows[0]["sku"] == "A1"
    assert "stray" not in rows[0]


@pytest.mark.asyncio
async def test_array_without_default_remains_header_only():
    """Behaviour is unchanged for schemas that don't declare ``default``."""
    fs = MockFileServiceClient()
    schema = [_table("orders", ["sku", "qty"])]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    art = find_artifact(result, "orders")
    body = await fs.download_file(art["file_id"])
    assert body.decode("utf-8").strip().split(",") == ["__row_id", "sku", "qty"]


@pytest.mark.asyncio
async def test_object_container_scalars_seed_root_csv_with_dotted_columns():
    """Scalars nested under tab/section wrappers land in the ``__root__``
    CSV as dotted-path columns — object containers no longer mint their
    own ``object_field`` artifacts. One seeded row carries the resolved
    defaults."""
    fs = MockFileServiceClient()
    schema = [{
        "key": "tabs", "outputType": "object",
        "fieldConfig": {"fieldWrapper": "TabWrapper"},
        "fields": [{
            "key": "tab_1", "outputType": "object",
            "fieldConfig": {"fieldWrapper": "TabItemWrapper"},
            "fields": [
                {"key": "templateName", "outputType": "string", "default": "Acme",
                 "fieldConfig": {"fieldWrapper": "FormItemWrapper"}},
                {"key": "status", "outputType": "string", "default": "ACTIVE",
                 "fieldConfig": {"fieldWrapper": "FormItemWrapper"}},
            ],
        }],
    }]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None, file_client=fs,
        node_id="n1", force_root=True,
    )
    # No per-container artifact any more — data lives on the root.
    assert find_artifact(result, "tabs.tab_1") is None
    assert not result.get("artifacts")
    assert result["file_id"]
    assert result["columns"] == [
        "__row_id", "tabs.tab_1.templateName", "tabs.tab_1.status",
    ]
    body = await fs.download_file(result["file_id"])
    header, rows = _read_csv(body)
    assert header == ["__row_id", "tabs.tab_1.templateName", "tabs.tab_1.status"]
    assert len(rows) == 1
    assert rows[0]["tabs.tab_1.templateName"] == "Acme"
    assert rows[0]["tabs.tab_1.status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_root_scalar_defaults_seed_root_csv():
    fs = MockFileServiceClient()
    schema = [
        {"key": "name", "outputType": "string", "default": "alice",
         "fieldConfig": {}},
        {"key": "count", "outputType": "number", "default": 7,
         "fieldConfig": {}},
    ]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    body = await fs.download_file(result["file_id"])
    header, rows = _read_csv(body)
    assert header == ["__row_id", "name", "count"]
    assert len(rows) == 1
    assert rows[0]["name"] == "alice"
    assert rows[0]["count"] == "7"
    assert rows[0]["__row_id"]


@pytest.mark.asyncio
async def test_existing_artifact_default_not_reapplied():
    """Q3 merge semantics: a pre-existing artifact keeps its file_id and is
    NOT re-seeded from the schema's ``default``."""
    fs = MockFileServiceClient()
    existing = build_file_ref(file_id="root_existing")
    add_artifact(
        existing, file_id="orders_existing", name="orders",
        artifact_id="orders", artifact_type="array_field",
    )
    table = _table("orders", ["sku"])
    table["default"] = [{"sku": "should-not-appear"}]
    schema = [table]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=existing,
        file_client=fs, node_id="n1",
    )
    assert find_artifact(result, "orders")["file_id"] == "orders_existing"
    # No upload happened for the existing artifact, so the seed never reached
    # any file. Sanity: download_file returns None for the unknown id.
    assert await fs.download_file("orders_existing") is None


@pytest.mark.asyncio
async def test_nested_array_default_is_deferred():
    """Nested arrays (depth > 0) cannot resolve __parent_row_id at provision
    time, so their ``default`` is intentionally not seeded — the CSV is
    header-only just like before."""
    fs = MockFileServiceClient()
    inner = _table("line_items", ["price"])
    inner["default"] = [{"price": "9.99"}]
    schema = [_table("orders", ["sku"], nested=[inner])]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1",
    )
    nested = find_artifact(result, "orders.line_items")
    body = await fs.download_file(nested["file_id"])
    header, rows = _read_csv(body)
    # The nested CSV body is header-only — the inner ``default`` is dropped.
    assert rows == []
    assert "price" in header
    # Artifact metadata still tracks ``__parent_row_id`` for downstream PATCH ops.
    assert "__parent_row_id" in nested["columns"]


class _UploadFailsFileClient(MockFileServiceClient):
    """File client whose ``upload_file`` always raises — simulates a
    file-service outage during provisioning."""

    async def upload_file(self, *args, **kwargs):  # type: ignore[override]
        raise RuntimeError("file service unavailable")


@pytest.mark.asyncio
async def test_root_stays_object_typed_when_upload_fails():
    """Regression: when the root CSV upload fails, the root must STILL be
    typed ``object_field`` (with an empty file_id), not a typeless placeholder.

    A typeless root routed PATCH /data ``__root__`` ops to the data-factory,
    which rejects the empty file_id with ``INVALID_INPUT`` → 400 "file_id is
    required". Stamping the type unconditionally lets PATCH route the root to
    the local object-upsert path, which self-mints a file on the first edit.
    """
    fs = _UploadFailsFileClient()
    schema = [
        {"key": "triggerId", "outputType": "string", "fieldConfig": {}},
    ]
    result = await provision_schema_artifacts(
        input_schema=schema, existing_ref=None,
        file_client=fs, node_id="n1", force_root=True,
    )
    assert is_file_ref(result)
    assert result["artifact_type"] == "object_field"  # typed even on failure
    assert result["content_type"] == "text/csv"
    assert not result["file_id"]  # upload failed → empty, but type is set
