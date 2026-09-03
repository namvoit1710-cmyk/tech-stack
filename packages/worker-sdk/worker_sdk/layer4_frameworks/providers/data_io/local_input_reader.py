"""Local filesystem input reader — reads task input data from disk."""

import mimetypes
import os
from collections.abc import AsyncIterator

from worker_sdk.layer1_domain.value_objects.chunk_options import ChunkOptions
from worker_sdk.layer1_domain.value_objects.data_metadata import DataMetadata
from worker_sdk.layer1_domain.value_objects.input_reference import InputReference
from worker_sdk.layer2_application.interfaces.input_reader_interface import IInputReader

_DEFAULT_DATA_DIR = "/tmp/worker-data"


def _resolve_path(uri: str) -> str:
    """Convert a file:// or data:// URI to an absolute filesystem path."""
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return parsed.path
    if parsed.scheme in ("", "data"):
        data_dir = os.environ.get("WORKER_DATA_DIR", _DEFAULT_DATA_DIR)
        path_part = parsed.path.lstrip("/")
        if parsed.netloc:
            path_part = f"{parsed.netloc}/{path_part}"
        return os.path.join(data_dir, path_part)
    return uri


class LocalInputReader(IInputReader):
    """Reads input data from the local filesystem."""

    async def fetch_all(self, ref: InputReference) -> bytes:
        path = _resolve_path(ref.uri)
        with open(path, "rb") as f:
            return f.read()

    async def read_chunked(
        self, ref: InputReference, options: ChunkOptions
    ) -> AsyncIterator[bytes]:
        path = _resolve_path(ref.uri)
        with open(path, "rb") as f:
            if options.offset:
                f.seek(options.offset)
            bytes_read = 0
            while True:
                remaining = None
                if options.limit is not None:
                    remaining = options.limit - bytes_read
                    if remaining <= 0:
                        break
                read_size = min(options.chunk_size, remaining) if remaining else options.chunk_size
                chunk = f.read(read_size)
                if not chunk:
                    break
                bytes_read += len(chunk)
                yield chunk

    async def stream(self, ref: InputReference) -> AsyncIterator[bytes]:
        async for chunk in self.read_chunked(ref, ChunkOptions()):
            yield chunk

    async def read_range(
        self, ref: InputReference, offset: int, length: int
    ) -> bytes:
        path = _resolve_path(ref.uri)
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    async def get_metadata(self, ref: InputReference) -> DataMetadata:
        path = _resolve_path(ref.uri)
        size = os.path.getsize(path)
        content_type, _ = mimetypes.guess_type(path)
        return DataMetadata(
            content_type=content_type or "application/octet-stream",
            size_bytes=size,
        )
