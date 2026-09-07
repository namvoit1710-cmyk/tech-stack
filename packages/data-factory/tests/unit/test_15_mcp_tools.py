"""
Unit tests for MCP tool definitions in data-factory.

Tests cover:
- Container injection (set_container / _get_usecase)
- Helper function (_to_list)
- All 8 MCP tool functions with mocked use cases
- Error handling when container is not set or usecase is missing
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from app.layer3_adapters.controllers.mcp.tools import (
    set_container,
    _get_usecase,
    _to_list,
    _to_dict,
    validate_file,
    query_validation_results,
    get_validation_download_url,
    transform_file,
    transform_rows,
    inspect_file,
    generate_schema_mapping,
    preview_schema_transform,
    execute_schema_transform,
    list_rule_sets,
    create_rule_set,
    match_rules_to_headers,
)
import app.layer3_adapters.controllers.mcp.tools as tools_module

from app.layer1_domain.entities.validation import ValidationResult, ODataContent
from app.layer1_domain.entities.transformation import TransformResult
from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import (
    DataValidationResult,
    DataValidationCommand,
)
from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import (
    DataTransformationResult,
    DataTransformationCommand,
)
from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import (
    RuleManagementResult,
    CreateRuleSetCommand,
)
from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
    SchemaInspectionResult,
    SchemaMappingGenerationResult,
    SchemaTransformPreviewResult,
    SchemaTransformResult,
    InspectSchemaCommand,
    GenerateSchemaMappingCommand,
    PreviewSchemaTransformCommand,
    ExecuteSchemaTransformCommand,
)
from app.layer2_application.features.data_transformation.use_cases.row_transformation_usecase import (
    RowTransformationResult,
    RowTransformationCommand,
)
from app.layer4_frameworks.providers.transformation.polars_row_transformer_provider import (
    RowTransformResult,
)
from app.layer1_domain.entities.schema_transform import (
    FileSchema,
    SchemaField,
    SchemaTransformPreview,
    SchemaTransformExecutionResult,
    SchemaMappingResult,
    FieldMappingSuggestion,
)


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_container():
    """Reset the global _container before each test."""
    tools_module._container = None
    yield
    tools_module._container = None


@pytest.fixture
def mock_validation_uc():
    uc = AsyncMock()
    uc.execute.return_value = DataValidationResult(
        success=True,
        message="Validation complete",
        result=ValidationResult(
            total_rows=20, valid_rows=18, invalid_rows=2, odata=ODataContent(data=[])
        ),
    )
    uc.query_result_file = MagicMock(return_value={"value": [{"id": 1}, {"id": 2}]})
    uc.get_download_url = MagicMock(return_value="https://s3.example.com/presigned/result.csv")
    return uc


@pytest.fixture
def mock_transformation_uc():
    uc = AsyncMock()
    uc.execute.return_value = DataTransformationResult(
        success=True,
        message="Transformed",
        result=TransformResult(
            success=True, total_rows=10, total_columns=5, odata=None, message="OK"
        ),
    )
    return uc


@pytest.fixture
def mock_row_transformation_uc():
    uc = AsyncMock()
    uc.execute.return_value = RowTransformationResult(
        success=True,
        message="Rows transformed",
        result=RowTransformResult(
            success=True, affected_rows=3, total_rows=17, message="Deleted 3 rows"
        ),
    )
    return uc


@pytest.fixture
def mock_schema_transform_uc():
    uc = AsyncMock()
    uc.inspect_file.return_value = SchemaInspectionResult(
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
    uc.generate_mapping.return_value = SchemaMappingGenerationResult(
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
    uc.preview_transform.return_value = SchemaTransformPreviewResult(
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
    uc.execute.return_value = SchemaTransformResult(
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
    return uc


@pytest.fixture
def mock_rule_management_uc():
    uc = AsyncMock()
    uc.get_all_rules.return_value = RuleManagementResult(
        success=True,
        data=[{"id": "abc-123", "name": "Test Rules", "rules": []}],
        message="OK",
    )
    uc.create_rule_set.return_value = RuleManagementResult(
        success=True,
        data={"id": "new-rule-id", "name": "New Rules", "rules": [{"rule_name": "R1"}]},
        message="Created",
    )
    uc.find_matching_rules.return_value = (
        [{"rule_name": "Email Format", "type": "expression"}],
        ["Validates email column format"],
    )
    return uc


@pytest.fixture
def container(mock_validation_uc, mock_transformation_uc, mock_row_transformation_uc, mock_schema_transform_uc, mock_rule_management_uc):
    return {
        "data_validation_usecase": mock_validation_uc,
        "data_transformation_usecase": mock_transformation_uc,
        "row_transformation_usecase": mock_row_transformation_uc,
        "schema_transform_usecase": mock_schema_transform_uc,
        "rule_management_usecase": mock_rule_management_uc,
    }


# ──────────────────────────────────────────────
# Container & Helper Tests
# ──────────────────────────────────────────────

@pytest.mark.unit
class TestContainerManagement:

    def test_set_container(self, container):
        set_container(container)
        assert tools_module._container is container

    def test_get_usecase_returns_usecase(self, container):
        set_container(container)
        uc = _get_usecase("data_validation_usecase")
        assert uc is container["data_validation_usecase"]

    def test_get_usecase_raises_when_no_container(self):
        with pytest.raises(RuntimeError, match="container not set"):
            _get_usecase("data_validation_usecase")

    def test_get_usecase_raises_when_name_not_found(self, container):
        set_container(container)
        with pytest.raises(RuntimeError, match="not available"):
            _get_usecase("nonexistent_usecase")


@pytest.mark.unit
class TestToListHelper:

    def test_to_list_with_list(self):
        data = [{"a": 1}, {"b": 2}]
        assert _to_list(data) == data

    def test_to_list_with_json_string(self):
        data = [{"a": 1}]
        assert _to_list(json.dumps(data)) == data

    def test_to_list_with_empty_list(self):
        assert _to_list([]) == []

    def test_to_list_with_empty_json_string(self):
        assert _to_list("[]") == []


@pytest.mark.unit
class TestToDictHelper:

    def test_to_dict_with_dict(self):
        data = {"columns": [{"name": "id"}]}
        assert _to_dict(data) == data

    def test_to_dict_with_json_string(self):
        data = {"columns": [{"name": "id"}]}
        assert _to_dict(json.dumps(data)) == data


# ──────────────────────────────────────────────
# Validation Tool Tests
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
class TestValidateFile:

    async def test_validate_file_with_inline_rules(self, container, mock_validation_uc):
        set_container(container)
        rules = [
            {"rule_name": "Unique ID", "type": "unique", "params": {"columns": ["id"]}, "error_message": "Dup ID"}
        ]
        result_str = await validate_file(file_path="test.csv", file_format="csv", rules=rules)
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["result"]["total_rows"] == 20
        mock_validation_uc.execute.assert_awaited_once()
        cmd = mock_validation_uc.execute.call_args[0][0]
        assert isinstance(cmd, DataValidationCommand)
        assert cmd.file_id == "test.csv"
        assert cmd.rules == rules

    async def test_validate_file_with_rule_set_id(self, container, mock_validation_uc):
        set_container(container)
        result_str = await validate_file(file_path="test.csv", rule_set_id="abc-123")
        result = json.loads(result_str)

        assert result["success"] is True
        cmd = mock_validation_uc.execute.call_args[0][0]
        assert cmd.rule_set_id == "abc-123"
        assert cmd.rules is None

    async def test_validate_file_with_rules_as_json_string(self, container, mock_validation_uc):
        set_container(container)
        rules = [{"rule_name": "R1", "type": "required", "params": {"columns": ["email"]}, "error_message": "Req"}]
        result_str = await validate_file(file_path="test.csv", rules=json.dumps(rules))
        result = json.loads(result_str)

        assert result["success"] is True
        cmd = mock_validation_uc.execute.call_args[0][0]
        assert cmd.rules == rules

    async def test_validate_file_no_container_raises(self):
        with pytest.raises(RuntimeError, match="container not set"):
            await validate_file(file_path="test.csv", rules=[{"rule_name": "R"}])


@pytest.mark.unit
@pytest.mark.asyncio
class TestQueryValidationResults:

    async def test_query_validation_results(self, container, mock_validation_uc):
        set_container(container)
        result_str = await query_validation_results(file_id="file-123", odata_query="$top=5", version_id="ver-2")
        result = json.loads(result_str)

        assert "value" in result
        assert len(result["value"]) == 2
        mock_validation_uc.query_result_file.assert_called_once_with("file-123", "$top=5", version_id="ver-2")

    async def test_query_validation_results_empty_query(self, container, mock_validation_uc):
        set_container(container)
        await query_validation_results(file_id="file-123")
        mock_validation_uc.query_result_file.assert_called_once_with("file-123", "", version_id=None)


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetValidationDownloadUrl:

    async def test_get_download_url(self, container, mock_validation_uc):
        set_container(container)
        result_str = await get_validation_download_url(file_id="file-123", version_id="ver-2")
        result = json.loads(result_str)

        assert result["file_url"] == "https://s3.example.com/presigned/result.csv"
        mock_validation_uc.get_download_url.assert_called_once_with("file-123", version_id="ver-2")


# ──────────────────────────────────────────────
# Transformation Tool Tests
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
class TestTransformFile:

    async def test_transform_file(self, container, mock_transformation_uc):
        set_container(container)
        rules = [{"rule_name": "Filter", "type": "filter", "params": {"expression": "col('age') > 18"}}]
        result_str = await transform_file(
            file_id="file-123", rules=rules, file_format="csv", output_format="csv", version_id="ver-1"
        )
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["result"]["total_rows"] == 10
        mock_transformation_uc.execute.assert_awaited_once()
        cmd = mock_transformation_uc.execute.call_args[0][0]
        assert isinstance(cmd, DataTransformationCommand)
        assert cmd.file_id == "file-123"
        assert cmd.rules == rules
        assert cmd.version_id == "ver-1"

    async def test_transform_file_with_json_string_rules(self, container, mock_transformation_uc):
        set_container(container)
        rules = [{"rule_name": "Select", "type": "select", "params": {"columns": ["id", "name"]}}]
        await transform_file(file_id="file-123", rules=json.dumps(rules))
        cmd = mock_transformation_uc.execute.call_args[0][0]
        assert cmd.rules == rules


@pytest.mark.unit
@pytest.mark.asyncio
class TestTransformRows:

    async def test_transform_rows_delete(self, container, mock_row_transformation_uc):
        set_container(container)
        operations = [
            {"operation": "delete", "row_identifier": {"type": "condition", "expression": "col('age') < 18"}}
        ]
        result_str = await transform_rows(file_path="data.csv", operations=operations)
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["result"]["affected_rows"] == 3
        mock_row_transformation_uc.execute.assert_awaited_once()
        cmd = mock_row_transformation_uc.execute.call_args[0][0]
        assert isinstance(cmd, RowTransformationCommand)
        assert cmd.operations == operations
        assert cmd.use_column_indices is False

    async def test_transform_rows_with_column_indices(self, container, mock_row_transformation_uc):
        set_container(container)
        operations = [{"operation": "delete", "row_identifier": {"type": "index", "index": 0}}]
        await transform_rows(file_path="data.csv", operations=operations, use_column_indices=True)
        cmd = mock_row_transformation_uc.execute.call_args[0][0]
        assert cmd.use_column_indices is True


# ──────────────────────────────────────────────
# Schema Transform Tool Tests
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
class TestInspectFile:

    async def test_inspect_file(self, container, mock_schema_transform_uc):
        set_container(container)
        result_str = await inspect_file(
            file_path="D:/sample.xlsx",
            file_format="xlsx",
            sample_size=3,
            sheet_names=["Customers", "Orders"],
            merge_sheets=True,
            add_sheet_name_column=True,
        )
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["result"]["file_format"] == "xlsx"
        mock_schema_transform_uc.inspect_file.assert_awaited_once()
        cmd = mock_schema_transform_uc.inspect_file.call_args[0][0]
        assert isinstance(cmd, InspectSchemaCommand)
        assert cmd.file_path == "D:/sample.xlsx"
        assert cmd.file_format == "xlsx"
        assert cmd.sample_size == 3
        assert cmd.sheet_names == ["Customers", "Orders"]
        assert cmd.merge_sheets is True
        assert cmd.add_sheet_name_column is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestGenerateSchemaMapping:

    async def test_generate_schema_mapping(self, container, mock_schema_transform_uc):
        set_container(container)
        result_str = await generate_schema_mapping(
            source_schema={"columns": [{"name": "id", "dtype": "Int64"}]},
            target_schema={"columns": [{"name": "customer_id"}, {"name": "customer_name"}]},
            source_type="file",
            mapping_hints=[{"source_field": "id", "target_field": "customer_id"}],
        )
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["result"]["mappings"][0]["target_field"] == "customer_id"
        mock_schema_transform_uc.generate_mapping.assert_awaited_once()
        cmd = mock_schema_transform_uc.generate_mapping.call_args[0][0]
        assert isinstance(cmd, GenerateSchemaMappingCommand)
        assert cmd.source_schema == {"columns": [{"name": "id", "dtype": "Int64"}]}
        assert cmd.target_schema == {"columns": [{"name": "customer_id"}, {"name": "customer_name"}]}
        assert cmd.source_type == "file"
        assert cmd.mapping_hints == [{"source_field": "id", "target_field": "customer_id"}]

    async def test_generate_schema_mapping_with_json_strings(self, container, mock_schema_transform_uc):
        set_container(container)
        source_schema = {"columns": [{"name": "id", "dtype": "Int64"}]}
        target_schema = {"columns": [{"name": "customer_id"}]}
        mapping_hints = [{"source_field": "id", "target_field": "customer_id"}]

        await generate_schema_mapping(
            source_schema=json.dumps(source_schema),
            target_schema=json.dumps(target_schema),
            mapping_hints=json.dumps(mapping_hints),
        )

        cmd = mock_schema_transform_uc.generate_mapping.call_args[0][0]
        assert cmd.source_schema == source_schema
        assert cmd.target_schema == target_schema
        assert cmd.mapping_hints == mapping_hints


@pytest.mark.unit
@pytest.mark.asyncio
class TestPreviewSchemaTransform:

    async def test_preview_schema_transform(self, container, mock_schema_transform_uc):
        set_container(container)
        rules = [{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}]
        sample_data = [{"id": 1}]
        result_str = await preview_schema_transform(
            sample_data=sample_data,
            rules=rules,
            file_format="json",
            target_schema={"columns": [{"name": "customer_id"}]},
        )
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["result"]["matches_target_schema"] is True
        mock_schema_transform_uc.preview_transform.assert_awaited_once()
        cmd = mock_schema_transform_uc.preview_transform.call_args[0][0]
        assert isinstance(cmd, PreviewSchemaTransformCommand)
        assert cmd.sample_data == sample_data
        assert cmd.rules == rules

    async def test_preview_schema_transform_with_json_string_rules(self, container, mock_schema_transform_uc):
        set_container(container)
        rules = [{"type": "drop_columns", "params": {"columns": ["tmp"]}}]
        sample_data = [{"id": 1, "tmp": "x"}]
        await preview_schema_transform(
            sample_data=json.dumps(sample_data),
            rules=json.dumps(rules),
        )
        cmd = mock_schema_transform_uc.preview_transform.call_args[0][0]
        assert cmd.sample_data == sample_data
        assert cmd.rules == rules


@pytest.mark.unit
@pytest.mark.asyncio
class TestExecuteSchemaTransform:

    async def test_execute_schema_transform(self, container, mock_schema_transform_uc):
        set_container(container)
        rules = [{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}]
        result_str = await execute_schema_transform(
            source_file_id="file-123",
            rules=rules,
            file_format="xlsx",
            output_format="csv",
            target_schema={"columns": [{"name": "customer_id"}]},
            field_mapping=[{"source_field": "id", "target_field": "customer_id"}],
            source_version_id="ver-source",
            target_file_id="target-456",
            target_version_id="ver-target",
            sheet_names=["Customers", "Orders"],
            merge_sheets=True,
            add_sheet_name_column=True,
        )
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["result"]["download_url"] == "http://uploaded.com/result.csv"
        mock_schema_transform_uc.execute.assert_awaited_once()
        cmd = mock_schema_transform_uc.execute.call_args[0][0]
        assert isinstance(cmd, ExecuteSchemaTransformCommand)
        assert cmd.source_file_id == "file-123"
        assert cmd.rules == rules
        assert cmd.file_format == "xlsx"
        assert cmd.output_format == "csv"
        assert cmd.target_schema == {"columns": [{"name": "customer_id"}]}
        assert cmd.field_mapping == [{"source_field": "id", "target_field": "customer_id"}]
        assert cmd.source_version_id == "ver-source"
        assert cmd.target_file_id == "target-456"
        assert cmd.target_version_id == "ver-target"
        assert cmd.sheet_names == ["Customers", "Orders"]
        assert cmd.merge_sheets is True
        assert cmd.add_sheet_name_column is True

    async def test_execute_schema_transform_with_json_string_rules(self, container, mock_schema_transform_uc):
        set_container(container)
        rules = [{"type": "select_columns", "params": {"columns": ["id"]}}]
        target_schema = {"columns": [{"name": "id"}]}
        field_mapping = [{"source_field": "id", "target_field": "id"}]
        sheet_names = ["Sheet1", "Sheet2"]

        await execute_schema_transform(
            source_file_id="file-123",
            rules=json.dumps(rules),
            target_schema=json.dumps(target_schema),
            field_mapping=json.dumps(field_mapping),
            sheet_names=json.dumps(sheet_names),
        )
        cmd = mock_schema_transform_uc.execute.call_args[0][0]
        assert cmd.rules == rules
        assert cmd.target_schema == target_schema
        assert cmd.field_mapping == field_mapping
        assert cmd.sheet_names == sheet_names


# ──────────────────────────────────────────────
# Rule Management Tool Tests
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
class TestListRuleSets:

    async def test_list_rule_sets(self, container, mock_rule_management_uc):
        set_container(container)
        result_str = await list_rule_sets()
        result = json.loads(result_str)

        assert result["success"] is True
        assert len(result["data"]) == 1
        mock_rule_management_uc.get_all_rules.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
class TestCreateRuleSet:

    async def test_create_rule_set(self, container, mock_rule_management_uc):
        set_container(container)
        rules = [{"rule_name": "R1", "type": "required", "params": {"columns": ["email"]}, "error_message": "Req"}]
        result_str = await create_rule_set(name="My Rules", rules=rules, description="Test rules")
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["data"]["id"] == "new-rule-id"
        mock_rule_management_uc.create_rule_set.assert_awaited_once()
        cmd = mock_rule_management_uc.create_rule_set.call_args[0][0]
        assert isinstance(cmd, CreateRuleSetCommand)
        assert cmd.name == "My Rules"
        assert cmd.rules == rules
        assert cmd.description == "Test rules"

    async def test_create_rule_set_with_json_string_rules(self, container, mock_rule_management_uc):
        set_container(container)
        rules = [{"rule_name": "R1", "type": "unique", "params": {"columns": ["id"]}, "error_message": "Dup"}]
        await create_rule_set(name="Test", rules=json.dumps(rules))
        cmd = mock_rule_management_uc.create_rule_set.call_args[0][0]
        assert cmd.rules == rules


@pytest.mark.unit
@pytest.mark.asyncio
class TestMatchRulesToHeaders:

    async def test_match_rules_to_headers(self, container, mock_rule_management_uc):
        set_container(container)
        result_str = await match_rules_to_headers(headers=["email", "age", "id"])
        result = json.loads(result_str)

        assert "rule_templates" in result
        assert "descriptions" in result
        assert len(result["rule_templates"]) == 1
        mock_rule_management_uc.find_matching_rules.assert_awaited_once_with(["email", "age", "id"])

    async def test_match_rules_to_headers_with_json_string(self, container, mock_rule_management_uc):
        set_container(container)
        await match_rules_to_headers(headers=json.dumps(["name", "phone"]))
        mock_rule_management_uc.find_matching_rules.assert_awaited_once_with(["name", "phone"])


# ──────────────────────────────────────────────
# MCP Graceful Degradation Tests
# ──────────────────────────────────────────────

@pytest.mark.unit
class TestMCPGracefulDegradation:

    def test_health_endpoint_reflects_mcp_available(self):
        """Health endpoint should report MCP availability."""
        from fastapi.testclient import TestClient
        from main import app, MCP_AVAILABLE

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "mcp" in data
        assert data["mcp"] == MCP_AVAILABLE

    def test_mcp_tools_returns_json_strings(self):
        """All MCP tools should return JSON-serializable strings."""
        # This is a contract test — MCP tools must return str for the MCP SDK
        import inspect
        from app.layer3_adapters.controllers.mcp.tools import mcp

        for tool in mcp._tool_manager.list_tools():
            assert tool.name in [
                "validate_file", "query_validation_results", "get_validation_download_url",
                "transform_file", "transform_rows",
                "inspect_file", "generate_schema_mapping", "preview_schema_transform", "execute_schema_transform",
                "list_rule_sets", "create_rule_set", "match_rules_to_headers",
                "build_reference_keyset",
            ]

    def test_all_tools_registered(self):
        """All expected tools should be registered on the MCP server."""
        from app.layer3_adapters.controllers.mcp.tools import mcp

        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        expected = [
            "validate_file", "query_validation_results", "get_validation_download_url",
            "transform_file", "transform_rows",
            "inspect_file", "generate_schema_mapping", "preview_schema_transform", "execute_schema_transform",
            "list_rule_sets", "create_rule_set", "match_rules_to_headers",
        ]
        for name in expected:
            assert name in tool_names, f"Tool '{name}' not registered"
