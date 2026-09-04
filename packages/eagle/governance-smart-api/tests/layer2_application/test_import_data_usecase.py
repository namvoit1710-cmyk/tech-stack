import asyncio

import pytest

from app.layer1_domain.entities.import_data import GovernanceImportAcceptance
from app.layer2_application.features.import_data.use_cases.import_data_usecase import (
    ImportDataCommand,
    ImportDataUseCase,
)


class _StubGateway:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def submit_import_job(self, request):
        self.requests.append(request)
        return GovernanceImportAcceptance(
            job_id="job-123",
            tenant_id=request.tenant_id or "default-tenant",
            status="accepted",
            file_ids=list(request.file_ids),
            accepted_at="2026-06-19T00:00:00+00:00",
            file_results=[],
        )


def test_import_data_usecase_normalizes_file_ids_and_delegates() -> None:
    gateway = _StubGateway()
    use_case = ImportDataUseCase(gateway)

    result = asyncio.run(
        use_case.execute(
            ImportDataCommand(
                file_ids=[" file-a ", "", "file-b", "file-a"],
                tenant_id="tenant-1",
            )
        )
    )

    assert result.job_id == "job-123"
    assert result.file_ids == ["file-a", "file-b"]
    assert gateway.requests[0].file_ids == ["file-a", "file-b"]
    assert gateway.requests[0].tenant_id == "tenant-1"


def test_import_data_usecase_rejects_empty_file_ids() -> None:
    use_case = ImportDataUseCase(_StubGateway())

    with pytest.raises(
        ValueError,
        match="file_ids must contain at least one non-empty file ID",
    ):
        asyncio.run(
            use_case.execute(
                ImportDataCommand(file_ids=["", "   "], tenant_id="tenant-1")
            )
        )


def test_import_data_usecase_rejects_empty_file_ids_list() -> None:
    # BOUNDARY: an entirely empty list is also invalid.
    use_case = ImportDataUseCase(_StubGateway())

    with pytest.raises(ValueError, match="at least one non-empty file ID"):
        asyncio.run(use_case.execute(ImportDataCommand(file_ids=[], tenant_id=None)))


def test_import_data_usecase_passes_through_none_tenant() -> None:
    # BOUNDARY: tenant_id=None is forwarded unchanged to the submitter.
    gateway = _StubGateway()
    use_case = ImportDataUseCase(gateway)

    result = asyncio.run(
        use_case.execute(ImportDataCommand(file_ids=["file-a"], tenant_id=None))
    )

    assert gateway.requests[0].tenant_id is None
    assert result.tenant_id == "default-tenant"
