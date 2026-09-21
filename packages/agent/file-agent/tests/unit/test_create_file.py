# from types import SimpleNamespace
# from unittest.mock import AsyncMock, MagicMock
# import os

# import pytest


# @pytest.mark.asyncio
# async def test_create_file_uploads_raw_csv_bytes_and_deletes_temp_file(monkeypatch):
#     from app.layer2_application.features import create_file as create_file_module

#     llm = type("MockLLM", (), {})()
#     llm.get_chat_completion = AsyncMock(
#         return_value='{"columns": ["Table", "Field", "Alias", "Section"], "rows": []}'
#     )
#     monkeypatch.setattr(create_file_module, "OpenAIService", lambda: llm)
#     monkeypatch.setattr(
#         create_file_module,
#         "AppSettingsAdapter",
#         lambda: SimpleNamespace(FILE_URL="https://files.example/"),
#     )

#     response = MagicMock()
#     response.raise_for_status = MagicMock()
#     response.json.return_value = {"file_id": "file-123"}

#     captured = {}

#     def fake_post(url, **kwargs):
#         captured["url"] = url
#         captured["params"] = kwargs["params"]
#         captured["headers"] = kwargs["headers"]
#         captured["body"] = kwargs["data"].read()
#         captured["temp_path"] = kwargs["data"].name
#         return response

#     monkeypatch.setattr(create_file_module.requests, "post", fake_post)

#     result = await create_file_module.create_file("table_field_alias_section", "Create a template CSV")

#     assert result == {
#         "message": "File uploaded successfully",
#         "download_url": "https://files.example/api/v1/download/file-123",
#         "file_id": "file-123",
#     }

#     assert captured["url"] == "https://files.example/api/v1/upload"
#     assert captured["params"] == {"filename": "table_field_alias_section.csv"}
#     assert captured["body"] == b"Table,Field,Alias,Section\r\n"
#     assert captured["headers"]["Content-Type"] == "text/csv"
#     assert not os.path.exists(captured["temp_path"])
