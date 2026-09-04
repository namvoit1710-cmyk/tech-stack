from typing import Protocol

from app.layer1_domain.entities.import_data import (
    GovernanceImportAcceptance,
    GovernanceImportRequest,
)


class IImportJobSubmitter(Protocol):
    async def submit_import_job(
        self,
        request: GovernanceImportRequest,
    ) -> GovernanceImportAcceptance: ...
