from typing import Protocol

from smart_service_sdk.layer1_domain.entities.background_job_record import (
    BackgroundJobRecord,
)


class IBackgroundJobRepository(Protocol):
    async def save(self, job: BackgroundJobRecord) -> BackgroundJobRecord: ...

    async def get(
        self,
        tenant_id: str,
        job_id: str,
    ) -> BackgroundJobRecord | None: ...

    async def list_by_status(self, status: str) -> list[BackgroundJobRecord]: ...
