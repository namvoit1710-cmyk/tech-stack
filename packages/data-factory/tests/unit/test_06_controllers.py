import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from app.layer3_adapters.controllers.restful.v1.data_validation_controller import router as validation_router
from app.layer3_adapters.controllers.restful.v1.data_transformation_controller import router as transformation_router
from app.layer3_adapters.controllers.restful.v1.schema_transform_controller import router as schema_transform_router
from app.layer3_adapters.controllers.restful.v1.rule_management_controller import router as rule_router
from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import DataValidationResult
from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import DataTransformationResult
from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
    SchemaInspectionResult,
    SchemaMappingGenerationResult,
    SchemaTransformPreviewResult,
    SchemaTransformResult,
)
from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import RuleManagementResult
from app.layer1_domain.entities.schema_transform import (
    FileSchema,
    SchemaField,
    SchemaTransformPreview,
    SchemaTransformExecutionResult,
    SchemaMappingResult,
    FieldMappingSuggestion,
)
from app.layer1_domain.entities.transformation import TransformResult

@pytest.mark.unit
def test_validation_controller_routes():
    """Test that validation controller routes are properly registered"""
    app = FastAPI()
    app.include_router(validation_router, prefix="/api/v1/validation")

    routes = list(app.openapi()["paths"])
    assert "/api/v1/validation" in routes
    assert "/api/v1/validation/result/query" in routes

@pytest.mark.unit
def test_transformation_controller_routes():
    """Test that transformation controller routes are properly registered"""
    app = FastAPI()
    app.include_router(transformation_router, prefix="/api/v1/transformation")

    routes = list(app.openapi()["paths"])
    assert "/api/v1/transformation" in routes

@pytest.mark.unit
def test_schema_transform_controller_routes():
    """Test that schema transform controller routes are properly registered"""
    app = FastAPI()
    app.include_router(schema_transform_router, prefix="/api/v1/schema-transform")

    routes = list(app.openapi()["paths"])
    assert "/api/v1/schema-transform" in routes
    assert "/api/v1/schema-transform/inspect" in routes
    assert "/api/v1/schema-transform/generate-mapping" in routes
    assert "/api/v1/schema-transform/preview" in routes

@pytest.mark.unit
def test_rule_management_controller_routes():
    """Test that rule management controller routes are properly registered"""
    app = FastAPI()
    app.include_router(rule_router, prefix="/api/v1/rules")

    routes = list(app.openapi()["paths"])
    assert "/api/v1/rules" in routes
    assert "/api/v1/rules/update/{rule_set_id}" in routes

@pytest.mark.unit
def test_controllers_have_correct_tags():
    """Test that controllers have appropriate tags"""
    app = FastAPI()
    app.include_router(validation_router, prefix="/api/v1/validation", tags=["Validation"])
    app.include_router(transformation_router, prefix="/api/v1/transformation", tags=["Transformation"])
    app.include_router(schema_transform_router, prefix="/api/v1/schema-transform", tags=["Schema Transform"])
    app.include_router(rule_router, prefix="/api/v1/rules", tags=["Rules"])

    # Check that routes have tags
    for route in app.routes:
        if hasattr(route, 'tags'):
            assert len(route.tags) > 0


# Comprehensive Controller Tests
@pytest.mark.unit
def test_validation_controller_endpoints():
    """Test validation controller endpoints functionality"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.execute.return_value = DataValidationResult(success=True, message="Validation completed")
    
    app.state.container = {"data_validation_usecase": mock_usecase}
    app.include_router(validation_router, prefix="/api/v1/validation")
    
    client = TestClient(app)
    
    # Test validation endpoint
    response = client.post("/api/v1/validation", json={
        "file_path": "test.csv",
        "version_id": "ver-1",
        "file_format": "csv",
        "rules": [{"type": "required", "params": {"columns": ["id"]}}]
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "Validation completed" in data["message"]
    mock_usecase.execute.assert_called_once()
    command = mock_usecase.execute.call_args.args[0]
    assert command.version_id == "ver-1"


@pytest.mark.unit
def test_validation_controller_result_query_uses_file_id_and_version_id():
    app = FastAPI()
    mock_usecase = MagicMock()
    mock_usecase.query_result_file.return_value = {"value": [{"id": 1}]}

    app.state.container = {"data_validation_usecase": mock_usecase}
    app.include_router(validation_router, prefix="/api/v1/validation")

    client = TestClient(app)

    response = client.get(
        "/api/v1/validation/result/query",
        params={"file_id": "file-123", "version_id": "ver-2", "$top": "1"},
    )

    assert response.status_code == 200
    mock_usecase.query_result_file.assert_called_once_with("file-123", "$top=1", version_id="ver-2")


@pytest.mark.unit
def test_validation_controller_result_download_uses_file_id_and_version_id():
    app = FastAPI()
    mock_usecase = MagicMock()
    mock_usecase.get_download_url.return_value = "http://example.test/download"

    app.state.container = {"data_validation_usecase": mock_usecase}
    app.include_router(validation_router, prefix="/api/v1/validation")

    client = TestClient(app)

    response = client.get(
        "/api/v1/validation/result/download",
        params={"file_id": "file-123", "version_id": "ver-2"},
    )

    assert response.status_code == 200
    assert response.json()["file_url"] == "http://example.test/download"
    mock_usecase.get_download_url.assert_called_once_with("file-123", version_id="ver-2")

@pytest.mark.unit
def test_transformation_controller_endpoints():
    """Test transformation controller endpoints functionality"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.execute.return_value = DataTransformationResult(success=True, message="Transformation completed")
    
    app.state.container = {"data_transformation_usecase": mock_usecase}
    app.include_router(transformation_router, prefix="/api/v1/transformation")
    
    client = TestClient(app)
    
    # Test transformation endpoint
    response = client.post("/api/v1/transformation", json={
        "file_path": "test.csv",
        "file_format": "csv",
        "rules": [{"type": "filter", "params": {"column": "status", "value": "active"}}]
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "Transformation completed" in data["message"]
    mock_usecase.execute.assert_called_once()


@pytest.mark.unit
def test_transformation_controller_returns_409_for_stale_version():
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.execute.return_value = DataTransformationResult(
        success=False,
        message="Requested file version is stale",
        result=TransformResult(
            success=False,
            total_rows=0,
            total_columns=0,
            odata=None,
            message="Requested file version is stale",
            source_file_id="file-123",
            source_version_id="ver-old",
            current_version_id="ver-new",
            stale_version=True,
        ),
    )

    app.state.container = {"data_transformation_usecase": mock_usecase}
    app.include_router(transformation_router, prefix="/api/v1/transformation")

    client = TestClient(app)

    response = client.post(
        "/api/v1/transformation",
        json={
            "file_id": "file-123",
            "file_format": "csv",
            "version_id": "ver-old",
            "rules": [{"type": "drop_columns", "params": {"columns_to_drop": ["address"]}}],
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["file_id"] == "file-123"
    assert detail["requested_version_id"] == "ver-old"
    assert detail["current_version_id"] == "ver-new"

@pytest.mark.unit
def test_schema_transform_controller_inspect():
    """Test schema transform inspect endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.inspect_file.return_value = SchemaInspectionResult(
        success=True,
        message="Schema inspected successfully",
        result=FileSchema(
            columns=[SchemaField(name="id", dtype="Int64")],
            sample_data=[{"id": 1}],
            nested_depth=1,
            file_format="xlsx",
            total_columns=1,
        ),
    )

    app.state.container = {"schema_transform_usecase": mock_usecase}
    app.include_router(schema_transform_router, prefix="/api/v1/schema-transform")

    client = TestClient(app)

    response = client.post("/api/v1/schema-transform/inspect", json={
        "file_path": "D:/sample.xlsx",
        "file_format": "xlsx",
        "sample_size": 3,
        "sheet_names": ["Customers", "Orders"],
        "merge_sheets": True,
        "add_sheet_name_column": True
    })

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["result"]["file_format"] == "xlsx"
    mock_usecase.inspect_file.assert_called_once()

@pytest.mark.unit
def test_schema_transform_controller_generate_mapping():
    """Test schema transform generate mapping endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.generate_mapping.return_value = SchemaMappingGenerationResult(
        success=True,
        message="Schema mapping generated successfully",
        result=SchemaMappingResult(
            mappings=[
                FieldMappingSuggestion(
                    source_field="id",
                    target_field="customer_id",
                    confidence=0.99,
                    reason="Exact normalized field-name match",
                )
            ],
            unmapped_source_fields=[],
            unmapped_target_fields=["customer_name"],
            suggested_rules=[
                {
                    "type": "rename_columns",
                    "params": {"mapping": {"id": "customer_id"}},
                }
            ],
            target_schema_columns=["customer_id", "customer_name"],
        ),
    )

    app.state.container = {"schema_transform_usecase": mock_usecase}
    app.include_router(schema_transform_router, prefix="/api/v1/schema-transform")

    client = TestClient(app)

    response = client.post("/api/v1/schema-transform/generate-mapping", json={
        "source_schema": {"columns": [{"name": "id", "dtype": "Int64"}]},
        "target_schema": {"columns": [{"name": "customer_id"}, {"name": "customer_name"}]},
        "source_type": "file",
        "mapping_hints": [{"source_field": "id", "target_field": "customer_id"}]
    })

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["result"]["mappings"][0]["target_field"] == "customer_id"
    mock_usecase.generate_mapping.assert_called_once()

@pytest.mark.unit
def test_schema_transform_controller_preview():
    """Test schema transform preview endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.preview_transform.return_value = SchemaTransformPreviewResult(
        success=True,
        message="Schema transform preview generated successfully",
        result=SchemaTransformPreview(
            preview_data=[{"customer_id": 1}],
            output_schema=FileSchema(
                columns=[SchemaField(name="customer_id", dtype="Int64")],
                sample_data=[{"customer_id": 1}],
                nested_depth=1,
                file_format="json",
                total_columns=1,
            ),
            matches_target_schema=True,
            missing_columns=[],
            unexpected_columns=[],
        ),
    )

    app.state.container = {"schema_transform_usecase": mock_usecase}
    app.include_router(schema_transform_router, prefix="/api/v1/schema-transform")

    client = TestClient(app)

    response = client.post("/api/v1/schema-transform/preview", json={
        "sample_data": [{"id": 1}],
        "rules": [{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        "file_format": "json",
        "target_schema": {"columns": [{"name": "customer_id"}]}
    })

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["result"]["matches_target_schema"] is True
    mock_usecase.preview_transform.assert_called_once()

@pytest.mark.unit
def test_schema_transform_controller_execute():
    """Test schema transform execute endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.execute.return_value = SchemaTransformResult(
        success=True,
        message="Schema transform executed successfully",
        result=SchemaTransformExecutionResult(
            success=True,
            total_rows=2,
            total_columns=1,
            output_schema=FileSchema(
                columns=[SchemaField(name="customer_id", dtype="Int64")],
                sample_data=[{"customer_id": 1}],
                nested_depth=1,
                file_format="csv",
                total_columns=1,
            ),
            preview_data=[{"customer_id": 1}],
            download_url="http://uploaded.com/result.csv",
            applied_rules=[{"type": "rename_columns"}],
        ),
    )

    app.state.container = {"schema_transform_usecase": mock_usecase}
    app.include_router(schema_transform_router, prefix="/api/v1/schema-transform")

    client = TestClient(app)

    response = client.post("/api/v1/schema-transform", json={
        "file_path": "D:/sample.xlsx",
        "rules": [{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        "file_format": "xlsx",
        "output_format": "csv",
        "target_schema": {"columns": [{"name": "customer_id"}]},
        "field_mapping": [{"source_field": "id", "target_field": "customer_id"}],
        "sheet_names": ["Customers", "Orders"],
        "merge_sheets": True,
        "add_sheet_name_column": True
    })

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["result"]["download_url"] == "http://uploaded.com/result.csv"
    mock_usecase.execute.assert_called_once()


@pytest.mark.unit
def test_schema_transform_controller_returns_409_for_stale_target_version():
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.execute.return_value = SchemaTransformResult(
        success=False,
        message="Requested file version is stale",
        result=SchemaTransformExecutionResult(
            success=False,
            total_rows=0,
            total_columns=0,
            output_schema=FileSchema(),
            source_file_id="source-file",
            source_version_id="ver-source",
            current_version_id="ver-target-new",
            stale_version=True,
            message="Requested file version is stale",
        ),
    )

    app.state.container = {"schema_transform_usecase": mock_usecase}
    app.include_router(schema_transform_router, prefix="/api/v1/schema-transform")

    client = TestClient(app)

    response = client.post(
        "/api/v1/schema-transform",
        json={
            "source_file_id": "source-file",
            "source_version_id": "ver-source",
            "target_file_id": "target-file",
            "target_version_id": "ver-target-old",
            "file_format": "csv",
            "output_format": "csv",
            "rules": [{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["source_file_id"] == "source-file"
    assert detail["requested_version_id"] == "ver-target-old"
    assert detail["current_version_id"] == "ver-target-new"


@pytest.mark.unit
def test_rule_management_controller_create():
    """Test rule management controller create endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.create_rule_set.return_value = RuleManagementResult(success=True, data=MagicMock(id="test-id"), message="Created")
    
    app.state.container = {"rule_management_usecase": mock_usecase}
    app.include_router(rule_router, prefix="/api/v1/rules")
    
    client = TestClient(app)
    
    # Test create rule set endpoint
    response = client.post("/api/v1/rules", json={
        "name": "Test Rule Set",
        "rules": [{"type": "required", "params": {"columns": ["id"]}}],
        "description": "Test description"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    mock_usecase.create_rule_set.assert_called_once()

@pytest.mark.unit
def test_rule_management_controller_get_all():
    """Test rule management controller get all endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.get_all_rules.return_value = RuleManagementResult(success=True, data=[MagicMock(id="1", name="Rule 1")])
    
    app.state.container = {"rule_management_usecase": mock_usecase}
    app.include_router(rule_router, prefix="/api/v1/rules")
    
    client = TestClient(app)
    
    # Test get all rule sets endpoint
    response = client.get("/api/v1/rules")
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    mock_usecase.get_all_rules.assert_called_once()

@pytest.mark.unit
def test_rule_management_controller_update():
    """Test rule management controller update endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.update_rule_set.return_value = RuleManagementResult(success=True, data=MagicMock(id="test-id"), message="Updated")
    
    app.state.container = {"rule_management_usecase": mock_usecase}
    app.include_router(rule_router, prefix="/api/v1/rules")
    
    client = TestClient(app)
    
    # Test update rule set endpoint
    response = client.put("/api/v1/rules/update/test-id", json={"rules": [{"type": "email"}]})
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    mock_usecase.update_rule_set.assert_called_once_with("test-id", [{"type": "email"}])

@pytest.mark.unit
def test_rule_management_controller_update_not_found():
    """Test rule management controller update endpoint when rule set not found"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.update_rule_set.return_value = MagicMock(success=False, message="RuleSet not found")
    
    app.state.container = {"rule_management_usecase": mock_usecase}
    app.include_router(rule_router, prefix="/api/v1/rules")
    
    client = TestClient(app)
    
    # Test update rule set endpoint with non-existent ID
    response = client.put("/api/v1/rules/update/non-existent", json={"rules": [{"type": "email"}]})
    
    assert response.status_code == 400
    data = response.json()
    assert data["detail"] == "RuleSet not found"

@pytest.mark.unit
def test_rule_management_controller_delete():
    """Test rule management controller delete endpoint"""
    app = FastAPI()
    mock_usecase = AsyncMock()
    mock_usecase.delete_rule_sets.return_value = RuleManagementResult(success=True, message="Deleted successfully")
    
    app.state.container = {"rule_management_usecase": mock_usecase}
    app.include_router(rule_router, prefix="/api/v1/rules")
    
    client = TestClient(app)
    
    # Test delete rule sets endpoint
    response = client.post("/api/v1/rules/delete", json={"ids": ["id1", "id2"]})
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    mock_usecase.delete_rule_sets.assert_called_once_with(["id1", "id2"])
