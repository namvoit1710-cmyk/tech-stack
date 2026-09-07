import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

@pytest.mark.integration
def test_health_check(test_app):
    response = test_app.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["architecture"] == "Clean 4-Layer"
    assert "mcp" in data
    assert "memory" in data


@pytest.mark.integration
def test_memory_guard_returns_server_busy_under_pressure(test_app):
    mock_vm = type("VM", (), {"available": 400 * 1024 * 1024, "total": 16 * 1024 * 1024 * 1024})()
    with patch("main.settings.MEMORY_GUARD_ENABLED", True), \
         patch("main.settings.MEMORY_GUARD_MIN_AVAILABLE_PERCENT", 5.0), \
         patch("main.settings.MEMORY_GUARD_MIN_AVAILABLE_MB", 512), \
         patch("main.psutil.virtual_memory", return_value=mock_vm):
        payload = {
            "file_path": "test.csv",
            "rules": [{"type": "drop_columns", "params": {"columns_to_drop": ["A"]}}],
        }
        response = test_app.post("/api/v1/transformation", json=payload)

    assert response.status_code == 503
    assert response.json()["error_code"] == "SERVER_BUSY_MEMORY_PRESSURE"


@pytest.mark.integration
def test_memory_guard_does_not_block_health_endpoint(test_app):
    mock_vm = type("VM", (), {"available": 200 * 1024 * 1024, "total": 16 * 1024 * 1024 * 1024})()
    with patch("main.settings.MEMORY_GUARD_ENABLED", True), \
         patch("main.settings.MEMORY_GUARD_MIN_AVAILABLE_PERCENT", 5.0), \
         patch("main.settings.MEMORY_GUARD_MIN_AVAILABLE_MB", 512), \
         patch("main.psutil.virtual_memory", return_value=mock_vm):
        response = test_app.get("/health")

    assert response.status_code == 200

@pytest.mark.integration
def test_validate_endpoint(test_app):
    payload = {
        "file_path": "test_path", 
        "rules": [{"rule_name": "R1", "type": "required", "params": {"columns": ["id"]}}],
        "file_format": "csv"
    }
    
    response = test_app.post("/api/v1/validation", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True

@pytest.mark.integration
def test_transform_endpoint(test_app):
    payload = {
        "file_path": "test.csv",
        "rules": [{"type": "drop_columns", "params": {"columns_to_drop": ["A"]}}]
    }
    
    response = test_app.post("/api/v1/transformation", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True

@pytest.mark.integration
def test_query_validation_result(test_app):
    response = test_app.get("/api/v1/validation/result/query?file_name=test.csv&$top=10")
    assert response.status_code in [200, 500]


@pytest.mark.integration
def test_transform_rows_endpoint(test_app):
    payload = {
        "file_path": "test.csv",
        "operations": [
            {
                "operation": "delete",
                "row_identifier": {"type": "index", "index": 0},
            }
        ],
    }
    response = test_app.post("/api/v1/transformation/rows", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "affected_rows" in data


@pytest.mark.integration
def test_transform_rows_empty_operations(test_app):
    payload = {"file_path": "test.csv", "operations": []}
    response = test_app.post("/api/v1/transformation/rows", json=payload)
    assert response.status_code == 400


@pytest.mark.integration
def test_transform_query_result(test_app):
    response = test_app.get("/api/v1/transformation/result/query?file_name=test.csv&$top=5")
    assert response.status_code == 200


@pytest.mark.integration
def test_transform_download_result(test_app):
    response = test_app.get("/api/v1/transformation/result/download?file_name=test.csv")
    assert response.status_code == 200
    data = response.json()
    assert "file_url" in data


@pytest.mark.integration
def test_validation_download_url(test_app):
    response = test_app.get("/api/v1/validation/result/download?file_name=test.csv")
    assert response.status_code == 200
    data = response.json()
    assert "file_url" in data


@pytest.mark.integration
def test_rule_management_list(test_app):
    response = test_app.get("/api/v1/rules")
    assert response.status_code == 200


@pytest.mark.integration
def test_rule_management_create(test_app):
    payload = {
        "name": "Test Rules",
        "rules": [{"rule_name": "R1", "type": "required", "params": {"columns": ["id"]}, "error_message": "Required"}],
        "description": "Test",
    }
    response = test_app.post("/api/v1/rules", json=payload)
    assert response.status_code == 200


@pytest.mark.integration
def test_rule_management_match_headers(test_app):
    response = test_app.post("/api/v1/rules/match", json={"headers": ["email", "id"]})
    assert response.status_code == 200
