from dataclasses import dataclass
from typing import Protocol, Any, Optional, List

from app.layer1_domain.exceptions import ConflictError


@dataclass(kw_only=True)
class StoredFileInfo:
    file_id: str
    version_id: str = ""
    download_url: str = ""
    filename: str = ""
    status: str = ""
    current_version_id: Optional[str] = None


class FileVersionConflictError(ConflictError):
    def __init__(
        self,
        file_id: str,
        requested_version_id: str,
        current_version_id: Optional[str] = None,
        message: Optional[str] = None,
    ):
        self.file_id = file_id
        self.requested_version_id = requested_version_id
        self.current_version_id = current_version_id
        super().__init__(
            message
            or (
                "The requested base version is no longer the latest version for this file. "
                f"file_id={file_id}, requested_version_id={requested_version_id}, "
                f"current_version_id={current_version_id or 'unknown'}"
            )
        )


class IFileStorageProvider(Protocol):
    """Protocol for robust NodeJS File Service Integration"""

    def generate_presigned_url(
        self,
        file_id: str,
        operation: str = "download",
        version_id: Optional[str] = None,
    ) -> str: ...

    def download_and_read(
        self,
        file_path: str,
        file_format: str,
        version_id: Optional[str] = None,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any: ...

    def upload_file(
        self,
        local_path: str,
        file_format: str = "csv",
        file_id: Optional[str] = None,
        version_id: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> StoredFileInfo: ...

    def upload_dataframe(
        self,
        df: Any,
        file_format: str = "csv",
        file_id: Optional[str] = None,
        version_id: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> StoredFileInfo: ...

    def download_to_local(self, file_path: str, file_format: str = "csv") -> str: ...
