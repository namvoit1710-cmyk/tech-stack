from typing import Protocol
from collections.abc import AsyncIterator

from worker_sdk.layer1_domain.value_objects.chunk_options import ChunkOptions
from worker_sdk.layer1_domain.value_objects.data_metadata import DataMetadata
from worker_sdk.layer1_domain.value_objects.input_reference import InputReference


class IInputReader(Protocol):
    async def fetch_all(self, ref: InputReference) -> bytes: ...
    async def read_chunked(
        self, ref: InputReference, options: ChunkOptions
    ) -> AsyncIterator[bytes]: ...
    async def stream(self, ref: InputReference) -> AsyncIterator[bytes]: ...
    async def read_range(
        self, ref: InputReference, offset: int, length: int
    ) -> bytes: ...
    async def get_metadata(self, ref: InputReference) -> DataMetadata: ...
