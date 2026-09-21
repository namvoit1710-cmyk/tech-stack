from __future__ import annotations

import tempfile
import json
import logging
from app.layer4_frameworks.file_service import FileService
import pandas as pd
from agent_sdk import OpenAIService
from app.layer2_application.features.execute_agent.prompt.header_detection_prompt import HEADER_DETECTION_PROMPT


class HeaderInferenceService:
    def __init__(self):
        self.open_ai_service = OpenAIService()
        self.file_service = FileService()

    async def get_header_by_llm(self, preview_text: str) -> dict[str, object]:
        """
        Use LLM to infer header structure from a file preview.
        """

        llm_response = await self._call_llm(preview_text)
        header_data = self._parse_llm_response(llm_response)

        header_index = header_data.get("header_row_index", 0)
        skip_after = header_data.get("skip_after_header", 0)
        header_columns = header_data.get("header_columns", [])

        return {
            "header": {
                "header_row_index": header_index,
                "skip_after_header": skip_after,
                "header_columns": header_columns,
            }
        }

    # ---------------- PRIVATE METHODS ---------------- #

    def _write_temp_file(self, file_bytes: bytes, extension: str) -> str:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp:
            tmp.write(file_bytes)
            return tmp.name

    async def _call_llm(self, preview_text: str) -> str:
        system_prompt = HEADER_DETECTION_PROMPT.replace("#sym:preview_text", preview_text)

        return await self.open_ai_service.get_chat_completion(
            system_prompt=system_prompt,
            user_prompt="",
        )

    def _parse_llm_response(self, llm_response: str) -> dict[str, object]:
        try:
            parsed = json.loads(llm_response)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logging.warning("Invalid JSON from LLM, defaulting to 0 values")
            return {
                "header_row_index": 0,
                "skip_after_header": 0
            }

    def _read_header_rows(self, file_path: str, extension: str, max_rows: int = 20) -> pd.DataFrame:
        if extension == ".csv":
            return pd.read_csv(file_path, header=None, nrows=max_rows)
        return pd.read_excel(file_path, header=None, nrows=max_rows)

    def _extract_header_columns(self, df: pd.DataFrame, header_index: int) -> list[dict[str, object]]:
        header_row = df.iloc[header_index].tolist()

        return [
            {"index": i, "name": str(col).strip()}
            for i, col in enumerate(header_row)
        ]
