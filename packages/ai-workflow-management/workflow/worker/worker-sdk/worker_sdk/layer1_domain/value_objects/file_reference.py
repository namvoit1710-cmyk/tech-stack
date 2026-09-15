from typing import Any

FILE_REF_MARKER = "__file_ref"


def is_file_ref(value: Any) -> bool:
    """Return True if *value* is a file-backed reference dict."""
    return isinstance(value, dict) and value.get(FILE_REF_MARKER) is True


def is_file_id_input(value: Any) -> bool:
    """Return True if *value* is a top-level file_id input (csv_single/csv_multi).

    The control plane sends ``{"file_id": "...", "data_mode": "csv_single"}``
    when data is stored as CSV in the file service.
    """
    return (
        isinstance(value, dict)
        and "file_id" in value
        and value.get("data_mode") in ("csv_single", "csv_multi")
    )
