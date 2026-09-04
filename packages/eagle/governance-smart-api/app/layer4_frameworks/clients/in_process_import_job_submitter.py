from app.layer1_domain.entities.import_data import GovernanceImportAcceptance
from smart_service_sdk.layer2_application.features.background_job.use_cases.background_job_usecases import (
    CreateBackgroundJobCommand,
    CreateBackgroundJobUseCase,
)


class InProcessImportJobSubmitter:
    def __init__(
        self,
        create_background_job_usecase: CreateBackgroundJobUseCase,
    ) -> None:
        self._create_background_job_usecase = create_background_job_usecase

    async def submit_import_job(self, request) -> GovernanceImportAcceptance:
        result = await self._create_background_job_usecase.execute(
            CreateBackgroundJobCommand(
                file_ids=list(request.file_ids),
                tenant_id=request.tenant_id,
            )
        )
        return GovernanceImportAcceptance(
            job_id=result.job_id,
            tenant_id=result.tenant_id,
            status=result.status,
            file_ids=list(result.file_ids),
            accepted_at=result.accepted_at,
            started_at=result.started_at,
            ended_at=result.ended_at,
            error_message=result.error_message,
            file_results=[
                {
                    "file_id": item.file_id,
                    "status": item.status,
                    "row_count": item.row_count,
                    "started_at": item.started_at,
                    "ended_at": item.ended_at,
                    "error_message": item.error_message,
                    "metadata": dict(item.metadata),
                }
                for item in result.file_results
            ],
        )
