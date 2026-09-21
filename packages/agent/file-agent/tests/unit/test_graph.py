"""
Unit tests for file-agent graph.py – targeting 90%+ coverage.
All external HTTP calls and OpenAI calls are patched.
"""

from __future__ import annotations

import io
import json
import os
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_openai_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_requests_response(status_code=200, content=b"col1,col2\n1,2\n", headers=None, text="", json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = text
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# get_str_file_content
# ---------------------------------------------------------------------------

# class TestGetStrFileContent:
#     def test_csv_returns_utf8_string(self):
#         from app.layer4_frameworks.file_service import FileService
#         file_service = FileService()
#         csv_bytes = b"a,b\n1,2\n3,4\n"
#         result = file_service.get_str_file_content(csv_bytes, ".csv")
#         assert "a,b" in result
#         assert "1,2" in result

#     def test_xlsx_returns_csv_string(self):
#         from app.layer4_frameworks.file_service import FileService
#         file_service = FileService()
#         # Build a minimal xlsx in memory
#         df = pd.DataFrame({"x": [10, 20], "y": [30, 40]})
#         buf = io.BytesIO()
#         df.to_excel(buf, index=False)
#         xlsx_bytes = buf.getvalue()

#         result = file_service.get_str_file_content(xlsx_bytes, ".xlsx")
#         assert "x" in result
#         assert "10" in result
