from __future__ import annotations

import pytest

from app.layer1_domain.exceptions import InvalidInputError
from app.layer2_application.features.query_file_data import (
    normalize_odata_query,
    query_file_data_impl,
)


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_odata_preview(self, file_id: str, num_rows: int) -> dict:
        return {}

    def query_odata_data(self, file_id: str, odata_query: str = "") -> dict:
        self.calls.append((file_id, odata_query))
        return {
            "data": [{"id": "1", "status": "active"}],
            "@odata.count": 1,
        }

    def download_file_content(self, file_id: str) -> bytes:
        return b""


def test_normalize_odata_query_adds_safe_defaults() -> None:
    result = normalize_odata_query("$filter=status eq 'active'&$select=id,status")

    assert "%24filter=status+eq+%27active%27" in result
    assert "%24select=id%2Cstatus" in result
    assert "%24top=50" in result
    assert "%24skip=0" in result
    assert "%24count=true" in result


def test_normalize_odata_query_accepts_plain_keys_and_mapping() -> None:
    result = normalize_odata_query({"filter": "status eq 'active'", "top": 5})

    assert "%24filter=status+eq+%27active%27" in result
    assert "%24top=5" in result


def test_normalize_odata_query_caps_top() -> None:
    result = normalize_odata_query("$top=999999", max_top=500)

    assert "%24top=500" in result


def test_normalize_odata_query_rejects_mutating_or_unknown_options() -> None:
    with pytest.raises(InvalidInputError):
        normalize_odata_query("$apply=delete")


def test_query_file_data_impl_calls_gateway_read_only() -> None:
    gateway = _Gateway()

    file_id = "file_550e8400-e29b-41d4-a716-446655440000"
    result = query_file_data_impl(
        file_id, "$filter=status eq 'active'", file_preview_gateway=gateway
    )

    assert result["read_only"] is True
    assert result["file_id"] == file_id
    assert result["data"] == [{"id": "1", "status": "active"}]
    assert gateway.calls
    assert gateway.calls[0][0] == file_id
    assert "%24filter=status+eq+%27active%27" in gateway.calls[0][1]


def test_query_file_data_impl_accepts_raw_uuid_file_ids_from_file_service() -> None:
    gateway = _Gateway()

    file_id = "550e8400-e29b-41d4-a716-446655440000"
    result = query_file_data_impl(file_id, "$top=1", file_preview_gateway=gateway)

    assert result["file_id"] == file_id
    assert gateway.calls[0][0] == file_id
