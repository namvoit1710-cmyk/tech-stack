from __future__ import annotations

from dataclasses import dataclass

from smart_service_sdk.layer2_application.features.background_job.background_job_coordinator import (
    BackgroundJobFileResult,
    BackgroundJobResult,
)


@dataclass(frozen=True)
class CreateBackgroundJobCommand:
    file_ids: list[str]
    tenant_id: str | None = None


@dataclass(frozen=True)
class GetBackgroundJobCommand:
    job_id: str
    tenant_id: str | None = None


class CreateBackgroundJobUseCase:
    def __init__(self, background_job_coordinator):
        self._background_job_coordinator = background_job_coordinator

    async def execute(
        self,
        command: CreateBackgroundJobCommand,
    ) -> BackgroundJobResult:
        return await self._background_job_coordinator.create_import_job(
            file_ids=command.file_ids,
            tenant_id=command.tenant_id,
        )


class GetBackgroundJobUseCase:
    def __init__(self, background_job_coordinator):
        self._background_job_coordinator = background_job_coordinator

    async def execute(
        self,
        command: GetBackgroundJobCommand,
    ) -> BackgroundJobResult:
        return await self._background_job_coordinator.get_background_job(
            job_id=command.job_id,
            tenant_id=command.tenant_id,
        )
