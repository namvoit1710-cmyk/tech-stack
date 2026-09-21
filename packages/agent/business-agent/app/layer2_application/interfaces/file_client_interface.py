from typing import Optional, Protocol
from app.layer1_domain.entities.file_client import FileData

class IFileClient(Protocol):
    async def get_data_by_odata(
        self,
        file_id: str,
        top: int = 3,
    ) -> FileData: ...

    async def download_file(
        self,
        file_id: str,
    ) -> Optional[bytes]: ...

    async def get_content_type(
        self,
        file_id: str,
    ) -> Optional[str]: ...
