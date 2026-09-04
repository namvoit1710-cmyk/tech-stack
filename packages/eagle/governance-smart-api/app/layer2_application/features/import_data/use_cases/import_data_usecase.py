from dataclasses import dataclass

from app.layer1_domain.entities.import_data import (
    GovernanceImportAcceptance,
    GovernanceImportRequest,
)
from app.layer2_application.interfaces.import_job_submitter_interface import (
    IImportJobSubmitter,
)


@dataclass(frozen=True)
class ImportDataCommand:
    file_ids: list[str]
    tenant_id: str | None = None


class ImportDataUseCase:
    def __init__(self, import_job_submitter: IImportJobSubmitter):
        self._import_job_submitter = import_job_submitter

    async def execute(self, command: ImportDataCommand) -> GovernanceImportAcceptance:
        normalized_file_ids = list(
            dict.fromkeys(str(file_id).strip() for file_id in command.file_ids if str(file_id).strip())
        )
        if not normalized_file_ids:
            raise ValueError("file_ids must contain at least one non-empty file ID")
        return await self._import_job_submitter.submit_import_job(
            GovernanceImportRequest(
                file_ids=normalized_file_ids,
                tenant_id=command.tenant_id,
            )
        )
