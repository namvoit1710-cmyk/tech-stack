"""Mock adapter for schema-transform service — reads CSV headers from in-memory file store."""
import csv
import io
from typing import Any

from app.layer2_application.interfaces.schema_transform_client_interface import ISchemaTransformClient


class MockSchemaTransformClient(ISchemaTransformClient):
    def __init__(self, file_service_client: Any=None) -> None:
        self._file_client = file_service_client

    async def extract_headers(self, file_id: str) -> list[dict[str, Any]]:
        if not self._file_client:
            return []
        raw = await self._file_client.download_file(file_id)
        if raw is None:
            return []
        try:
            text = raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return []
        reader = csv.reader(io.StringIO(text))
        try:
            header_row = next(reader)
        except StopIteration:
            return []
        result = []
        for i, name in enumerate(header_row):
            col_letter = ""
            n = i
            while True:
                col_letter = chr(65 + n % 26) + col_letter
                n = n // 26 - 1
                if n < 0:
                    break
            result.append({"name": name, "index": col_letter})
        return result
