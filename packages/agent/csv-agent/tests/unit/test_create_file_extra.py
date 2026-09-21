import asyncio
import csv
import json
import os
from unittest.mock import AsyncMock

from app.layer2_application.features import create_file as module


def test_create_file_helpers_parse_and_sanitize():
    assert module.sanitize_create_filename("../bad name") == "bad_name.csv"
    assert module.sanitize_create_filename("") == "generated_data.csv"
    assert "Return ONLY valid JSON" in module.build_llm_prompt("make data")
    assert module.parse_llm_response('{"columns": ["a"], "rows": [[1]]}') == {
        "columns": ["a"],
        "rows": [[1]],
    }


def test_create_csv_from_data_writes_expected_rows():
    path = module.create_csv_from_data(["a", "b"], [[1, 2]])
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows == [["a", "b"], ["1", "2"]]
    finally:
        os.remove(path)


def test_create_file_service_handles_invalid_llm_json():
    llm = type("L", (), {"generate_completion": AsyncMock(return_value="not-json")})()
    uploader = type("U", (), {"upload_file": lambda self, path: {"file_id": "file"}})()

    result = asyncio.run(module.create_file_service("out", "desc", llm, uploader))

    assert result["error"] == "LLM returned invalid JSON"


def test_create_file_service_handles_upload_error_payload():
    llm = type(
        "L",
        (),
        {
            "generate_completion": AsyncMock(
                return_value=json.dumps({"columns": ["a"], "rows": [[1]]})
            )
        },
    )()
    uploader = type(
        "U", (), {"upload_file": lambda self, path: {"error": "upstream"}}
    )()

    result = asyncio.run(module.create_file_service("out", "desc", llm, uploader))

    assert result == {"error": "upstream"}
