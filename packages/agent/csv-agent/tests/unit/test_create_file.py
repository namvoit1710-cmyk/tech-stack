import asyncio
import os
from unittest.mock import AsyncMock

from app.layer2_application.features.create_file import create_file_service


def test_create_file_service_success_creates_and_cleans_temp_file():
    llm_adapter = type("MockLLMAdapter", (), {})()
    llm_adapter.generate_completion = AsyncMock(
        return_value='{"columns": ["Table", "Field"], "rows": [["A", "B"]]}'
    )

    captured = {}

    class _Uploader:
        def upload_file(self, file_path: str):
            captured["file_path"] = file_path
            captured["exists_during_upload"] = os.path.exists(file_path)
            return {"file_id": "file_123"}

    result = asyncio.run(
        create_file_service(
            filename="template",
            description="Create simple csv",
            llm_adapter=llm_adapter,
            file_upload_adapter=_Uploader(),
        )
    )

    assert result["message"] == "File created and uploaded successfully"
    assert result["file_id"] == "file_123"
    assert result["rows_count"] == 1
    assert result["columns_count"] == 2
    assert captured["exists_during_upload"] is True
    assert os.path.exists(captured["file_path"]) is False


def test_create_file_service_returns_error_when_missing_input():
    llm_adapter = type("MockLLMAdapter", (), {})()
    llm_adapter.generate_completion = AsyncMock()

    class _Uploader:
        def upload_file(self, file_path: str):
            return {"file_id": "file_123"}

    result = asyncio.run(
        create_file_service(
            filename="",
            description="",
            llm_adapter=llm_adapter,
            file_upload_adapter=_Uploader(),
        )
    )

    assert result == {"error": "Missing filename or description"}
