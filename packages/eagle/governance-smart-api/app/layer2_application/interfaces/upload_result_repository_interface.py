from typing import Protocol

from app.layer1_domain.entities.import_data import GovernanceUploadResult


class IUploadResultRepository(Protocol):
    async def save(self, result: GovernanceUploadResult) -> GovernanceUploadResult: ...
