from typing import Protocol


class IFileServiceClient(Protocol):
    async def download_file(
        self,
        file_id: str,
        tenant_id: str | None = None,
        *,
        fallback_file_name: str | None = None,
        fallback_content_type: str | None = None,
    ) -> tuple[str, str, str]: ...

    def parse_csv_rows(self, file_path: str) -> list[dict[str, str]]: ...

    def content_hash(self, file_path: str) -> str: ...
