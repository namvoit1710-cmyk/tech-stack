from typing import Protocol
from collections.abc import AsyncIterator

from worker_sdk.layer1_domain.value_objects.output_reference import OutputReference
from worker_sdk.layer1_domain.value_objects.write_options import WriteOptions


class IOutputWriter(Protocol):
    async def write(
        self, ref: OutputReference, data: bytes, options: WriteOptions
    ) -> None: ...
    async def create_multipart(
        self, ref: OutputReference, parts: list[bytes], options: WriteOptions
    ) -> None: ...
    async def create_stream(
        self, ref: OutputReference, stream: AsyncIterator[bytes], options: WriteOptions
    ) -> None: ...
    async def write_batch(
        self,
        refs: list[OutputReference],
        data_list: list[bytes],
        options: WriteOptions,
    ) -> None: ...
