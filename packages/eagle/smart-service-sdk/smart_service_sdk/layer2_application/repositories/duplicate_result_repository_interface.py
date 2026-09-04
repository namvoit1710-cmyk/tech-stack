from typing import Protocol

from smart_service_sdk.layer1_domain.entities.duplicate_result import DuplicateResult


class IDuplicateResultRepository(Protocol):
    async def save_duplicate_result(self, result: DuplicateResult) -> DuplicateResult: ...
