import os

from app.layer2_application.features.create_file import (
    create_csv_from_data,
    sanitize_create_filename,
)


def test_sanitize_create_filename_prevents_path_traversal_and_forces_csv_extension():
    result = sanitize_create_filename("../../unsafe name")

    assert "/" not in result
    assert ".." not in result
    assert result.endswith(".csv")


def test_create_csv_from_data_writes_header_and_rows_then_cleanup():
    path = create_csv_from_data(["a", "b"], [[1, 2], [3, 4]])

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "a,b" in content
        assert "1,2" in content
        assert "3,4" in content
    finally:
        if os.path.exists(path):
            os.remove(path)
