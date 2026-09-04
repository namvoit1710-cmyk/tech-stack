import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from datetime import datetime

from app.layer1_domain.entities.import_data import GovernanceImportAcceptance
from app.layer2_application.features.import_data.use_cases.import_data_usecase import (
    ImportDataUseCase,
)
from app.layer2_application.features.import_data.use_cases.upload_import_file_usecase import (
    UploadImportFileUseCase,
)
from main import create_app


class _StubGateway:
    async def submit_import_job(self, request):
        return GovernanceImportAcceptance(
            job_id="job-123",
            tenant_id=request.tenant_id or "default-tenant",
            status="accepted",
            file_ids=list(request.file_ids),
            accepted_at="2026-06-19T00:00:00+00:00",
            file_results=[],
        )


class _StubUploadResultRepository:
    async def save(self, result):
        result.created_at = datetime(2026, 6, 22, 12, 0, 0)
        return result


class _StubEmbeddingProvider:
    async def embed_texts(self, texts):
        return [[float(index + 1)] for index, _ in enumerate(texts)]


class _StubRetrievalChunkRepository:
    async def replace_file_chunks(self, tenant_id, file_id, documents, chunks):
        del tenant_id, file_id, documents, chunks

    async def upsert_file_sync_state(self, tenant_id, state):
        del tenant_id, state


def test_governance_health_route_returns_ok() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_governance_import_route_uses_layered_usecase() -> None:
    app = create_app()
    with TestClient(app) as client:
        client.app.state.container["import_data_usecase"] = ImportDataUseCase(_StubGateway())
        response = client.post(
            "/governance/import-data",
            json={"file_ids": ["file-a", "file-b"], "tenant_id": "tenant-1"},
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-123"
    assert response.json()["file_ids"] == ["file-a", "file-b"]


def test_governance_upload_route_accepts_multipart_file() -> None:
    app = create_app()
    with TestClient(app) as client:
        client.app.state.container["upload_import_file_usecase"] = UploadImportFileUseCase(
            _StubUploadResultRepository(),
            _StubEmbeddingProvider(),
            _StubRetrievalChunkRepository(),
            "default-tenant",
        )
        response = client.post(
            "/governance/import-data/upload",
            files={"file": ("records.csv", b"name,city\nacme,Bangkok\n", "text/csv")},
            data={"tenant_id": "tenant-1"},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["result_id"].startswith("gov-upload-")
    assert payload["file_name"] == "records.csv"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["row_count"] == 1
    assert payload["raw_headers"] == ["name", "city"]
    assert payload["parsed_rows"] == [{"name": "acme", "city": "Bangkok"}]


def test_governance_upload_ui_console_route_returns_html() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/governance/ui/upload-file")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/governance/import-data/upload" in response.text
    assert "Governance upload console" in response.text
    assert "/api/v1/import-jobs/" not in response.text


def test_governance_import_ui_console_route_returns_html() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/governance/import-data")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="file-id-list"' in response.text
    assert 'id="tenant-id"' in response.text
    assert 'fetch("/governance/import-data"' in response.text
    assert "/governance/import-data/upload" not in response.text


def test_governance_old_ui_route_is_not_available() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/governance/ui")

    assert response.status_code == 404
