from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from app.layer1_domain.entities.transformation import (
    TransformationErrorResult,
    TransformationSuccessResult,
)


class _Settings:
    AGENT_TYPE = "file"


def _make_app(data_factory_service):
    from app.layer3_adapters.controllers.restful.v1.transform_controller import (
        router,
    )

    app = FastAPI()
    app.state.dependencies = {
        "data_factory_service": data_factory_service,
        "settings": _Settings(),
    }
    app.include_router(router, prefix="/api/v1")
    return app


def test_transformations_endpoint_uses_data_factory_service():
    data_factory_service = MagicMock()
    data_factory_service.execute.return_value = TransformationSuccessResult(
        message="Executed 2 transformation batches.",
        file_id="file-2",
        download_url="https://example.com/download/file-2",
        batch_count=2,
    )
    client = TestClient(_make_app(data_factory_service))

    response = client.post(
        "/api/v1/transformations",
        json={
            "file_id": "file-0",
            "rules": [
                {
                    "scope": "column",
                    "rule_name": "rule-1",
                    "type": "mapping",
                    "params": {},
                },
                {
                    "scope": "row",
                    "operation": "delete",
                    "row_identifier": {"type": "index", "index": 0},
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "message": "Your file transformations finished successfully. Download the updated file here: https://example.com/download/file-2",
        "status": "success",
        "agent_type": "file",
        "agent_data": {
            "agent_type": "file",
            "valid": "correct",
            "message": "Your file transformations finished successfully. Download the updated file here: https://example.com/download/file-2",
            "file_id": "file-2",
            "download_url": "https://example.com/download/file-2",
            "batch_count": 2,
        },
        "transport_state": "RECEIVED",
    }
    data_factory_service.execute.assert_called_once_with(
        file_id="file-0",
        rules=[
            {
                "scope": "column",
                "rule_name": "rule-1",
                "type": "mapping",
                "params": {},
            },
            {
                "scope": "row",
                "operation": "delete",
                "row_identifier": {"type": "index", "index": 0},
            },
        ],
    )


def test_transformations_endpoint_returns_502_when_service_fails():
    data_factory_service = MagicMock()
    data_factory_service.execute.return_value = TransformationErrorResult(
        error="API error",
        failed_batch_index=2,
    )
    client = TestClient(_make_app(data_factory_service))

    response = client.post(
        "/api/v1/transformations",
        json={
            "file_id": "file-0",
            "rules": [
                {
                    "scope": "column",
                    "rule_name": "rule-1",
                    "type": "mapping",
                    "params": {},
                }
            ],
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "message": "We couldn't complete your file transformation request. API error",
        "status": "error",
        "agent_type": "file",
        "agent_data": {
            "agent_type": "file",
            "valid": "incorrect",
            "message": "We couldn't complete your file transformation request. API error",
            "error": "API error",
            "failed_batch_index": 2,
        },
        "transport_state": "RECEIVED",
    }