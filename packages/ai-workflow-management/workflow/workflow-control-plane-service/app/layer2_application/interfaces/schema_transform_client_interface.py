from typing import Any, Protocol


class ISchemaTransformClient(Protocol):
    """Port for extracting file headers via the schema-transform service."""

    async def extract_headers(self, file_id: str) -> list[dict[str, Any]]:
        """Extract column headers from a file.

        Returns list of dicts: [{"name": "col_name", "index": "A"}, ...]
        Returns empty list on failure.
        """
        ...
