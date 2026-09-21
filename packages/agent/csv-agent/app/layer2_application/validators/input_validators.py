import re
from typing import Any, Optional

from app.layer1_domain.exceptions import InvalidInputError

_SAFE_FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


def validate_file_id(file_id: Any, field_name: str = "file_id") -> str:
    """
    Validate an opaque File Service file identifier.

    The old implementation only accepted ``file_<uuid>``. In practice, the file
    service/data-factory path can return either ``file_<uuid>``, a raw UUID, or
    another safe opaque identifier. This validator accepts those safe IDs while
    still rejecting path traversal, query strings, fragments, whitespace, and
    empty values.
    """
    if not file_id:
        raise InvalidInputError(field_name, "Cannot be empty or null", file_id)

    if not isinstance(file_id, str):
        raise InvalidInputError(
            field_name, f"Must be string, got {type(file_id).__name__}", file_id
        )

    normalized = file_id.strip()
    if not normalized:
        raise InvalidInputError(field_name, "Cannot be empty or whitespace", file_id)

    if normalized != file_id:
        raise InvalidInputError(
            field_name, "Must not contain leading or trailing whitespace", file_id
        )

    if not _SAFE_FILE_ID_PATTERN.match(normalized):
        raise InvalidInputError(
            field_name,
            "Invalid file identifier. Use a safe opaque ID without slash, backslash, query string, fragment, or spaces.",
            file_id,
        )

    return normalized


def validate_non_empty_string(
    value: Any, field_name: str, max_length: Optional[int] = None
) -> str:
    """
    Validate that a value is a non-empty string.

    Args:
        value: Value to validate
        field_name: Name of the field for error messages
        max_length: Optional maximum length constraint

    Returns:
        str: Validated string

    Raises:
        InvalidInputError: If validation fails
    """
    if not value:
        raise InvalidInputError(field_name, "Cannot be empty or null", value)

    if not isinstance(value, str):
        raise InvalidInputError(
            field_name, f"Must be string, got {type(value).__name__}", value
        )

    value = value.strip()
    if not value:
        raise InvalidInputError(
            field_name, "Cannot be empty after trimming whitespace", value
        )

    if max_length and len(value) > max_length:
        raise InvalidInputError(
            field_name,
            f"Too long, maximum {max_length} characters allowed",
            value[:100] + "..." if len(value) > 100 else value,
        )

    return value


def validate_json_data(json_data: Any, field_name: str = "json_data") -> str:
    """
    Validate JSON data input.
    """
    if not json_data:
        raise InvalidInputError(field_name, "Cannot be empty or null", json_data)

    if not isinstance(json_data, str):
        raise InvalidInputError(
            field_name, f"Must be string, got {type(json_data).__name__}", json_data
        )

    if len(json_data) > 50_000_000:  # 50MB
        raise InvalidInputError(
            field_name,
            "JSON data too large, maximum 50MB allowed",
            f"{len(json_data)} bytes",
        )

    return json_data


def validate_file_format(format_str: Any, field_name: str = "file_format") -> str:
    """
    Validate file format.
    """
    if not format_str:
        raise InvalidInputError(field_name, "Cannot be empty or null", format_str)

    if not isinstance(format_str, str):
        raise InvalidInputError(
            field_name, f"Must be string, got {type(format_str).__name__}", format_str
        )

    allowed_formats = {"csv", "json", "xlsx"}
    normalized = format_str.strip().lower()

    if normalized not in allowed_formats:
        raise InvalidInputError(
            field_name,
            f"Invalid format '{format_str}', allowed: {', '.join(sorted(allowed_formats))}",
            format_str,
        )

    return normalized
