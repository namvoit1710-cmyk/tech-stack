import os
import re
from typing import Tuple, Optional
import csv
import requests
import pandas as pd
import logging
import io
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


class FileService:
    """Client for interacting with the file-service."""

    def __init__(self):
        settings = AppSettingsAdapter()
        self.base_url = settings.FILE_URL

    def download(self, file_key: str) -> Tuple[bytes, str]:
        """
        Download a file from file-service.

        Returns:
            (file_bytes, extension)
        """
        url = f"{self.base_url}/api/v1/download/{file_key}"
        response = requests.get(url)

        if response.status_code == 404:
            raise FileNotFoundError(f"File not found in file-service: {file_key!r}")

        if response.status_code >= 400:
            raise RuntimeError(
                f"File-service download failed ({response.status_code}): "
                f"{response.text[:500]}"
            )

        extension = self._resolve_extension(response)

        if extension not in {".csv", ".xlsx"}:
            raise ValueError(
                f"Unsupported file type for {file_key!r}: extension={extension!r}"
            )

        return response.content, extension

    @staticmethod
    def _resolve_extension(response: requests.Response) -> str:
        """Infer file extension from headers or content."""

        # Prefer content-type
        content_type = (
            (response.headers.get("content-type") or "")
            .split(";")[0]
            .strip()
            .lower()
        )

        if content_type == "text/csv":
            return ".csv"

        if content_type in {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
        }:
            return ".xlsx"

        # Try filename from Content-Disposition
        content_disposition = response.headers.get("content-disposition", "")
        filename_match = re.search(r'filename="([^"]+)"', content_disposition)

        if filename_match:
            filename = filename_match.group(1)
            extension = os.path.splitext(filename)[1].lower()
            if extension:
                return extension

        # Fallback: inspect file content
        body = response.content

        if body.startswith(b"PK\x03\x04"):
            return ".xlsx"

        try:
            sample = body[:1024].decode("utf-8")
            if "," in sample or "\n" in sample:
                return ".csv"
        except UnicodeDecodeError:
            pass

        return ""
    
    def upload_file(self, file_bytes: bytes, filename: str) -> str:
        url = f"{self.base_url}/api/v1/upload?filename={filename}"

        file = {
            "file": ("file", file_bytes)
        }

        response = requests.post(url, files=file)

        if response.status_code >= 400:
            raise RuntimeError(
                f"File-service upload failed ({response.status_code}): {response.text[:500]}"
            )

        return response.json()["file_id"]
    
    def convert_to_csv_bytes(self, file_bytes: bytes, extension: str) -> Tuple[bytes, str]:
        extension = extension.lstrip(".").lower()

        if extension == "xlsx":
            logging.info("File extension=%s, converting to CSV", extension)
            # Read Excel from bytes
            df = pd.read_excel(io.BytesIO(file_bytes))

            # Convert to CSV in memory
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)

            return csv_buffer.getvalue().encode("utf-8"), "csv"

        return file_bytes, extension
    
    def get_str_file_content(self, file_bytes: bytes, extension : str) -> str:
        if extension == ".csv":
            return file_bytes.decode("utf-8")

        if extension == ".xlsx":
            df = pd.read_excel(io.BytesIO(file_bytes))
            return df.to_csv(index=False)

        raise ValueError(f"Unsupported file extension: {extension!r}")
        
    def convert_to_csv_and_upload(self, file_bytes: bytes, extension: str) -> Optional[Tuple[str, bytes, str]]:
        extension = extension.lstrip(".").lower()
        # Only convert and upload if not CSV
        if extension != "csv":
            file_bytes, extension = self.convert_to_csv_bytes(file_bytes, extension)
            file_id = self.upload_file(file_bytes, "uploaded_file")
            return file_id, file_bytes, "csv"
        return None
    
    def get_csv_shape(self, file_bytes: bytes) -> Tuple[int, int]:
        num_cols = 0
        # Read only the first line to determine column count
        first_newline = file_bytes.find(b'\n')
        if first_newline != -1:
            first_line = file_bytes[:first_newline].decode("utf-8")
            num_cols = len(next(csv.reader([first_line])))
        # Count rows by counting newlines (avoid full decode)
        num_rows = file_bytes.count(b'\n')
        # Adjust if file doesn't end with newline
        if file_bytes and not file_bytes.endswith(b'\n'):
            num_rows += 1

        return num_rows, num_cols
    
    def read_preview_rows(self, file_path: str, extension: str, num_rows: int = 10) -> str:
        """Read the first N rows of a CSV or XLSX file and return them as quoted CSV text."""
        
    
        if extension == ".csv":
            df = pd.read_csv(file_path, header=None, nrows=num_rows)
        else:
            df = pd.read_excel(file_path, header=None, nrows=num_rows)
    
        rows = []
        for _, row in df.iterrows():
            row_str = ",".join(f'"{str(v)}"' for v in row)
            rows.append(row_str)
        return "\n".join(rows)
    
    def fetch_preview_file_data(self, file_id: str, top: int = 10, skip: int = 0, count: bool = True) -> dict:
        """
        Fetch file data from the remote API using the given file_id.
        Returns the parsed JSON response.
        """
        import requests
        url = f"{self.base_url}/api/v1/odata/{file_id}/data?$top={top}&$skip={skip}&$count={'true' if count else 'false'}"
        resp = requests.get(url)
        resp.raise_for_status()
        return resp.json()