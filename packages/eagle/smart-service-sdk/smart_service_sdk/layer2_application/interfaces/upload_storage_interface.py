from typing import BinaryIO, Protocol


class IUploadStorage(Protocol):
    async def store(
        self,
        file_name: str,
        content: BinaryIO,
        *,
        tenant_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> tuple[str, str]: ...

    async def delete(self, file_id: str, *, tenant_id: str | None = None) -> None: ...
