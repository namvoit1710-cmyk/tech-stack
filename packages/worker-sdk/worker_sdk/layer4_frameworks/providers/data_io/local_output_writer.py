"""Local filesystem output writer — writes task output data to disk."""

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from worker_sdk.layer1_domain.value_objects.output_reference import OutputReference
from worker_sdk.layer1_domain.value_objects.write_options import WriteOptions
from worker_sdk.layer2_application.interfaces.output_writer_interface import IOutputWriter

_DEFAULT_DATA_DIR = "/tmp/worker-data"


def _resolve_path(uri: str) -> str:
    """Convert a file:// URI or plain path to an absolute filesystem path."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return parsed.path
    if parsed.scheme in ("", "data"):
        # data://task-id/field.json → {DATA_DIR}/task-id/field.json
        data_dir = os.environ.get("WORKER_DATA_DIR", _DEFAULT_DATA_DIR)
        path_part = parsed.path.lstrip("/")
        if parsed.netloc:
            path_part = f"{parsed.netloc}/{path_part}"
        return os.path.join(data_dir, path_part)
    return uri


class LocalOutputWriter(IOutputWriter):
    """Writes output data to the local filesystem."""

    async def write(
        self, ref: OutputReference, data: bytes, options: WriteOptions
    ) -> None:
        path = _resolve_path(ref.uri)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if options.overwrite else "xb"
        try:
            with open(path, mode) as f:
                f.write(data)
        except FileExistsError:
            # File already exists and overwrite=False — overwrite anyway
            # to avoid breaking the flow on retries.
            with open(path, "wb") as f:
                f.write(data)

    async def create_multipart(
        self, ref: OutputReference, parts: list[bytes], options: WriteOptions
    ) -> None:
        await self.write(ref, b"".join(parts), options)

    async def create_stream(
        self, ref: OutputReference, stream: AsyncIterator[bytes], options: WriteOptions
    ) -> None:
        path = _resolve_path(ref.uri)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            async for chunk in stream:
                f.write(chunk)

    async def write_batch(
        self,
        refs: list[OutputReference],
        data_list: list[bytes],
        options: WriteOptions,
    ) -> None:
        for ref, data in zip(refs, data_list):
            await self.write(ref, data, options)
