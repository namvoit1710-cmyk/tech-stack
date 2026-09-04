from types import SimpleNamespace

import pytest

from app.bootstrap import build_governance_dependencies
from app.layer2_application.features.import_data.use_cases.import_data_usecase import (
    ImportDataUseCase,
)
from app.layer2_application.features.import_data.use_cases.upload_import_file_usecase import (
    UploadImportFileUseCase,
)
from app.layer4_frameworks.clients.in_process_import_job_submitter import (
    InProcessImportJobSubmitter,
)
from app.layer4_frameworks.logger.app_logger import AppLogger


class _StubCreateBackgroundJobUseCase:
    async def execute(self, command):  # pragma: no cover - satisfies hasattr check only
        return command


def _full_container(settings: object | None = None) -> dict:
    return {
        "create_background_job_usecase": _StubCreateBackgroundJobUseCase(),
        "duplicate_result_repository": object(),
        "result_repository": object(),
        "embedding_provider": object(),
        "retrieval_chunk_repository": object(),
        "runtime_configuration_service": object(),
        "settings": settings,
    }


def test_build_governance_dependencies_wires_all_use_cases() -> None:
    settings = SimpleNamespace(
        LOG_LEVEL="DEBUG", LOG_FORMAT="json", DEFAULT_TENANT_ID="  tenant-x  "
    )

    deps = build_governance_dependencies(_full_container(settings))

    assert isinstance(deps["logger"], AppLogger)
    assert isinstance(deps["import_job_submitter"], InProcessImportJobSubmitter)
    assert isinstance(deps["import_data_usecase"], ImportDataUseCase)
    assert isinstance(deps["upload_import_file_usecase"], UploadImportFileUseCase)
    # DEFAULT_TENANT_ID is stripped when wiring the upload use case.
    assert deps["upload_import_file_usecase"]._default_tenant_id == "tenant-x"


def test_build_governance_dependencies_defaults_when_settings_missing() -> None:
    deps = build_governance_dependencies(_full_container(settings=None))

    assert isinstance(deps["logger"], AppLogger)
    # No settings -> empty default tenant, logger falls back to INFO/text.
    assert deps["upload_import_file_usecase"]._default_tenant_id == ""


def test_build_governance_dependencies_requires_background_job_usecase() -> None:
    container = _full_container()
    container["create_background_job_usecase"] = None

    with pytest.raises(KeyError, match="create_background_job_usecase"):
        build_governance_dependencies(container)


def test_build_governance_dependencies_rejects_background_job_without_execute() -> None:
    container = _full_container()
    container["create_background_job_usecase"] = object()  # lacks .execute

    with pytest.raises(KeyError, match="create_background_job_usecase"):
        build_governance_dependencies(container)
