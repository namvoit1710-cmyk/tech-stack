import asyncio

import pytest
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from app.bootstrap import build_governance_dependencies
from main import create_app


def test_composed_app_keeps_sdk_routes() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/governance/import-data" in paths
    assert "/governance/import-data/upload" in paths
    assert "/governance/ui/upload-file" in paths
    assert "/governance/ui" not in paths
    assert "/api/v1/import-jobs" in paths
    assert "/api/v1/duplicate-check" not in paths


def test_build_governance_dependencies_maps_sdk_import_usecase() -> None:
    class _SdkResultFile:
        file_id = "file-z"
        status = "accepted"
        row_count = 0
        started_at = None
        ended_at = None
        error_message = ""
        metadata = {}

    class _SdkCreateUseCase:
        async def execute(self, command):
            assert command.file_ids == ["file-z"]
            assert command.tenant_id == "tenant-9"
            return type(
                "_SdkResult",
                (),
                {
                    "job_id": "job-789",
                    "tenant_id": "tenant-9",
                    "status": "accepted",
                    "file_ids": ["file-z"],
                    "accepted_at": "2026-06-19T01:02:03+00:00",
                    "started_at": None,
                    "ended_at": None,
                    "error_message": "",
                    "file_results": [_SdkResultFile()],
                },
            )()

    shared_container = {
        "settings": type(
            "_Settings",
            (),
            {
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "text",
            },
        )(),
        "create_background_job_usecase": _SdkCreateUseCase(),
        "embedding_provider": object(),
        "retrieval_chunk_repository": object(),
        "result_repository": type(
            "_ResultRepository",
            (),
            {"save": lambda self, result: result},
        )(),
    }
    dependencies = build_governance_dependencies(shared_container)
    result = asyncio.run(
        dependencies["import_job_submitter"].submit_import_job(
            request=type(
                "_Request",
                (),
                {"file_ids": ["file-z"], "tenant_id": "tenant-9"},
            )()
        )
    )

    assert result.job_id == "job-789"
    assert result.file_ids == ["file-z"]
    assert dependencies["import_data_usecase"].__class__.__name__ == "ImportDataUseCase"
    assert dependencies["upload_import_file_usecase"].__class__.__name__ == "UploadImportFileUseCase"
