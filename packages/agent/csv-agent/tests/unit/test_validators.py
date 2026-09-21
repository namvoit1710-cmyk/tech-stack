import pytest

from app.layer1_domain.exceptions import InvalidInputError
from app.layer2_application.validators.input_validators import (
    validate_file_format,
    validate_file_id,
    validate_json_data,
    validate_non_empty_string,
)


def test_validate_file_id_accepts_safe_opaque_ids():
    assert (
        validate_file_id("file_550e8400-e29b-41d4-a716-446655440000")
        == "file_550e8400-e29b-41d4-a716-446655440000"
    )
    assert (
        validate_file_id("550e8400-e29b-41d4-a716-446655440000")
        == "550e8400-e29b-41d4-a716-446655440000"
    )
    assert validate_file_id("abc.def:ghi-1") == "abc.def:ghi-1"


@pytest.mark.parametrize(
    "value", [None, "", " file_1", "file 1", "../file", "file?x=1", 123]
)
def test_validate_file_id_rejects_unsafe_values(value):
    with pytest.raises(InvalidInputError):
        validate_file_id(value)


def test_validate_non_empty_string_trims_and_enforces_length():
    assert validate_non_empty_string("  abc  ", "name", max_length=5) == "abc"
    with pytest.raises(InvalidInputError):
        validate_non_empty_string("abcdef", "name", max_length=3)
    with pytest.raises(InvalidInputError):
        validate_non_empty_string(123, "name")
    with pytest.raises(InvalidInputError):
        validate_non_empty_string("   ", "name")


def test_validate_json_data_accepts_string_and_rejects_bad_values():
    assert validate_json_data("[]") == "[]"
    with pytest.raises(InvalidInputError):
        validate_json_data({"a": 1})
    with pytest.raises(InvalidInputError):
        validate_json_data("")


def test_validate_file_format_normalizes_known_formats():
    assert validate_file_format(" CSV ") == "csv"
    assert validate_file_format("xlsx") == "xlsx"
    with pytest.raises(InvalidInputError):
        validate_file_format("parquet")
    with pytest.raises(InvalidInputError):
        validate_file_format(None)
