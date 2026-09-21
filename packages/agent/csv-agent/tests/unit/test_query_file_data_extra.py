import pytest

from app.layer1_domain.exceptions import FileProcessingError, InvalidInputError
from app.layer2_application.features import query_file_data as module


def test_normalize_odata_query_rejects_empty_parameter_name():
    with pytest.raises(InvalidInputError):
        module.normalize_odata_query({"": "value"})


def test_normalize_odata_query_rejects_unsupported_option():
    with pytest.raises(InvalidInputError):
        module.normalize_odata_query({"$expand": "Orders"})


def test_normalize_odata_query_rejects_bad_sequence_item():
    with pytest.raises(InvalidInputError):
        module.normalize_odata_query([("$select", "Name"), ("broken",)])


def test_normalize_odata_query_rejects_unsupported_type():
    with pytest.raises(InvalidInputError):
        module.normalize_odata_query(123)


def test_normalize_odata_query_clamps_top_and_rejects_invalid_values():
    with pytest.raises(InvalidInputError):
        module.normalize_odata_query({"top": "bad"}, default_top=7, max_top=10)
    assert "%24top=10" in module.normalize_odata_query(
        {"$top": "999"}, default_top=7, max_top=10
    )
    with pytest.raises(InvalidInputError):
        module.normalize_odata_query({"$top": "-5"}, default_top=7, max_top=10)


class _ListGateway:
    def query_odata_data(self, file_id: str, odata_query: str):
        return [{"id": 1}]


class _FailingGateway:
    def query_odata_data(self, file_id: str, odata_query: str):
        raise RuntimeError("upstream down")


def test_query_file_data_wraps_non_dict_payload():
    result = module.query_file_data_impl(
        "file_123", {"select": "id"}, file_preview_gateway=_ListGateway()
    )

    assert result["data"] == [{"id": 1}]
    assert result["file_id"] == "file_123"
    assert result["read_only"] is True


def test_query_file_data_wraps_gateway_error():
    with pytest.raises(FileProcessingError):
        module.query_file_data_impl(
            "file_123", "?$top=1", file_preview_gateway=_FailingGateway()
        )
