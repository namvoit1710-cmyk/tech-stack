"""Tests for DataStoreService — store/materialize round-trip (pointer-only refs)."""

import pytest

from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.data_reference import is_file_ref, FILE_REF_MARKER
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


@pytest.fixture
def service():
    return DataStoreService(MockFileServiceClient(), CsvConverter())


class TestStoreData:
    async def test_flat_dict_round_trip(self, service):
        data = {"name": "Alice", "age": 30}
        ref = await service.store_data("node1", data)

        assert is_file_ref(ref)
        assert ref["file_id"]
        assert ref["row_count"] == 1
        assert ref["content_type"] == "text/csv"

        # No-``extracted`` model (Stage 5): file_refs are pointer-only — no
        # extracted key. Scalar values resolve on demand via the strategy chain.
        assert "extracted" not in ref

    async def test_materialize_reconstructs_data(self, service):
        data = {"name": "Bob", "score": 95}
        ref = await service.store_data("node1", data)

        materialized = await service.materialize(ref)
        assert materialized["name"] == "Bob"
        assert materialized["score"] == 95

    async def test_nested_dict_materialize(self, service):
        data = {
            "output": {"result": 42},
            "metadata": {"type": "task"},
            "status_code": 200,
        }
        ref = await service.store_data("task1", data)
        materialized = await service.materialize(ref)

        assert materialized["output"]["result"] == 42
        assert materialized["metadata"]["type"] == "task"
        assert materialized["status_code"] == 200

    async def test_list_of_dicts(self, service):
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        ref = await service.store_data("items", data)

        assert is_file_ref(ref)
        assert ref["row_count"] == 2

        materialized = await service.materialize(ref)
        assert isinstance(materialized, list)
        assert len(materialized) == 2
        assert materialized[0]["name"] == "Alice"
        assert materialized[1]["name"] == "Bob"

    async def test_empty_data(self, service):
        ref = await service.store_data("empty", {})
        # Empty data returns a file ref with empty file_id
        assert is_file_ref(ref)
        assert ref["file_id"] == ""

    def test_is_file_ref_helpers(self, service):
        assert service.is_file_ref({FILE_REF_MARKER: True, "file_id": "x"})
        assert not service.is_file_ref({"output": {"uri": "x"}})
        assert not service.is_file_ref("string")
        assert not service.is_file_ref(42)
        assert not service.is_file_ref(None)

    async def test_columns_populated(self, service):
        data = {"output": {"result": 1}, "status_code": 200}
        ref = await service.store_data("node1", data)
        assert "output.result" in ref["columns"]
        assert "status_code" in ref["columns"]

    # NOTE (Stage 5): ``test_nested_dict_round_trip``, ``test_scalar_in_nested_output``,
    # ``test_extracted_contains_list_values``, and the two
    # ``test_large_*_marked_requires_fetch`` tests were deleted — they pinned the
    # removed ``extracted`` inline-cache / ``requires_fetch`` behaviour. Round-trip
    # coverage lives in ``test_*_materialize`` / ``test_list_of_dicts``; the
    # pointer-only contract is guarded by ``test_flat_dict_round_trip``.


class TestTypeFidelity:
    """Write-time ``column_types`` make the CSV round-trip lossless, so ID-like
    strings survive instead of being guessed into numbers on read."""

    async def test_ref_carries_column_types_schema(self, service):
        # The schema is captured from the real Python types at write time.
        ref = await service.store_data(
            "node1", {"code": "0001", "qty": 5, "active": True, "tags": ["a"]}
        )
        assert ref["column_types"] == {
            "code": "string",
            "qty": "number",
            "active": "boolean",
            "tags": "json",
        }

    async def test_id_like_string_preserved_through_round_trip(self, service):
        # The headline guarantee: a leading-zero code and a version string come
        # back verbatim, while genuine numbers/booleans still restore as typed.
        data = {"code": "0001", "version": "1.20", "qty": 5, "active": True}
        ref = await service.store_data("node1", data)

        out = await service.materialize(ref)
        assert out["code"] == "0001"   # not 1
        assert out["version"] == "1.20"  # not 1.2
        assert out["qty"] == 5 and isinstance(out["qty"], int)
        assert out["active"] is True

    async def test_array_field_artifact_preserves_id_strings(self, service):
        # Array-of-objects fields are split into a separate artifact CSV; that
        # path must preserve types too. (A scalar field is included so the main
        # ref gets a real file_id — materialize re-attaches artifacts only then.)
        data = {"status": "ok", "rows": [{"code": "0007"}, {"code": "0042"}]}
        ref = await service.store_data("node2", data)
        art = next(a for a in ref["artifacts"] if a["name"] == "rows")
        assert art["column_types"] == {"code": "string"}

        out = await service.materialize(ref)
        assert [r["code"] for r in out["rows"]] == ["0007", "0042"]

    async def test_legacy_ref_without_schema_still_materializes(self, service):
        # A ref written before this change has no ``column_types``; materialize
        # must not crash and falls back to the heuristic (back-compat).
        ref = await service.store_data("node1", {"name": "Bob", "code": "0001"})
        ref.pop("column_types", None)

        out = await service.materialize(ref)
        assert out["name"] == "Bob"
        assert out["code"] == 1  # heuristic fallback (documents the old behaviour)
