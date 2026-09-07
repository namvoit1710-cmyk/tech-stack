import pytest
from unittest.mock import AsyncMock, MagicMock
from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import DataValidationUseCase, DataValidationCommand, DataValidationResult
from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import DataTransformationUseCase, DataTransformationCommand, DataTransformationResult
from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import RuleManagementUseCase, CreateRuleSetCommand, RuleManagementResult
from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
    SchemaTransformUseCase,
    InspectSchemaCommand,
    GenerateSchemaMappingCommand,
    PreviewSchemaTransformCommand,
    ExecuteSchemaTransformCommand,
)
from app.layer1_domain.entities.rule_management import RuleSet
from app.layer1_domain.entities.validation import ValidationResult, ODataContent
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
@pytest.mark.asyncio
async def test_validate_cloud_data_success(mock_logger, mock_file_reader, mock_validator):
    mock_rule_repo = AsyncMock()
    mock_odata_parser = MagicMock()

    uc = DataValidationUseCase(mock_logger, mock_validator, mock_rule_repo, mock_file_reader, mock_odata_parser)
    req = DataValidationCommand(file_id="test.csv", rules=[{"rule_name": "R1", "type": "required", "params": {}}])

    mock_validator.validate_cloud_file.return_value = ValidationResult(
        total_rows=100, valid_rows=95, invalid_rows=5, odata=ODataContent(data=[])
    )

    res = await uc.execute(req)

    assert res.success is True
    assert res.result is not None
    assert res.result.invalid_rows == 5
    mock_validator.validate_cloud_file.assert_called_once()
@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_cloud_data_failure(mock_logger, mock_file_reader, mock_validator):
    mock_rule_repo = AsyncMock()
    mock_odata_parser = MagicMock()
    mock_validator.validate_cloud_file.side_effect = Exception("Engine Failure")

    uc = DataValidationUseCase(mock_logger, mock_validator, mock_rule_repo, mock_file_reader, mock_odata_parser)

    res = await uc.execute(DataValidationCommand(file_id="test.csv", rules=[{"rule_name": "R1", "type": "required", "params": {}}]))
    assert res.success is False
    assert "Engine Failure" in res.message

@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_cloud_data_threads_version_id(mock_logger, mock_file_reader, mock_validator):
    mock_rule_repo = AsyncMock()
    mock_odata_parser = MagicMock()

    uc = DataValidationUseCase(mock_logger, mock_validator, mock_rule_repo, mock_file_reader, mock_odata_parser)
    req = DataValidationCommand(
        file_id="file-123",
        version_id="ver-1",
        file_format="csv",
        rules=[{"rule_name": "R1", "type": "required", "params": {}}],
    )

    mock_validator.validate_cloud_file.return_value = ValidationResult(
        total_rows=100, valid_rows=95, invalid_rows=5, odata=ODataContent(data=[])
    )

    await uc.execute(req)

    call_args = mock_validator.validate_cloud_file.await_args
    assert call_args.args[0] == "file-123"
    assert call_args.args[1] == "csv"
    assert call_args.kwargs["version_id"] == "ver-1"

@pytest.mark.unit
@pytest.mark.asyncio
async def test_transform_cloud_data(mock_logger, mock_file_reader, mock_transformer, mock_file_upload):
    mock_odata_parser = MagicMock()

    uc = DataTransformationUseCase(mock_logger, mock_transformer, mock_file_reader, mock_odata_parser)
    req = DataTransformationCommand(file_id="test.csv", rules=[{"type": "filter", "params": {}}])

    res = await uc.execute(req)

    assert res.success is True
    mock_transformer.transform_cloud_file.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transform_cloud_data_threads_version_id(mock_logger, mock_file_reader, mock_transformer):
    mock_odata_parser = MagicMock()

    uc = DataTransformationUseCase(mock_logger, mock_transformer, mock_file_reader, mock_odata_parser)
    req = DataTransformationCommand(
        file_id="file-123",
        file_format="csv",
        output_format="csv",
        version_id="ver-1",
        rules=[{"type": "drop_columns", "params": {"columns_to_drop": ["address"]}}],
    )

    await uc.execute(req)

    call_args = mock_transformer.transform_cloud_file.await_args.args
    assert call_args[0] == "file-123"
    assert call_args[1] == "csv"
    assert call_args[2] == "csv"
    assert call_args[4] == "ver-1"

@pytest.mark.unit
@pytest.mark.asyncio
async def test_inspect_schema_success(mock_logger):
    mock_transformer = MagicMock()
    mock_transformer.inspect_schema.return_value = FileSchema(
        columns=[SchemaField(name="id", dtype="Int64")],
        sample_data=[{"id": 1}],
        nested_depth=1,
        file_format="xlsx",
        total_columns=1,
    )

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    req = InspectSchemaCommand(file_path="D:/sample.xlsx", file_format="xlsx", sample_size=3)

    res = await uc.inspect_file(req)

    assert res.success is True
    assert res.result is not None
    assert res.result.file_format == "xlsx"
    mock_transformer.inspect_schema.assert_called_once_with(
        "D:/sample.xlsx",
        "xlsx",
        3,
        None,
        None,
        False,
        False,
        None,
    )

@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_schema_mapping_success(mock_logger):
    mock_transformer = MagicMock()
    mock_transformer.generate_mapping.return_value = SchemaMappingResult(
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
        message="Generated 1 field mappings for source type 'file'",
    )

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    req = GenerateSchemaMappingCommand(
        source_schema={"columns": [{"name": "id", "dtype": "Int64"}]},
        target_schema={"columns": [{"name": "customer_id"}, {"name": "customer_name"}]},
        source_type="file",
    )

    res = await uc.generate_mapping(req)

    assert res.success is True
    assert res.result is not None
    assert res.result.mappings[0].target_field == "customer_id"
    mock_transformer.generate_mapping.assert_called_once_with(
        {"columns": [{"name": "id", "dtype": "Int64"}]},
        {"columns": [{"name": "customer_id"}, {"name": "customer_name"}]},
        "file",
        None,
    )

@pytest.mark.unit
@pytest.mark.asyncio
async def test_preview_schema_transform_success(mock_logger):
    mock_transformer = MagicMock()
    mock_transformer.preview_schema_transform.return_value = SchemaTransformPreview(
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
    )

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    req = PreviewSchemaTransformCommand(
        sample_data=[{"id": 1}],
        rules=[{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        file_format="json",
        target_schema={"columns": [{"name": "customer_id"}]},
    )

    res = await uc.preview_transform(req)

    assert res.success is True
    assert res.result is not None
    assert res.result.matches_target_schema is True
    mock_transformer.preview_schema_transform.assert_called_once()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_schema_transform_success(mock_logger):
    mock_transformer = AsyncMock()
    mock_transformer.execute_schema_transform.return_value = SchemaTransformExecutionResult(
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
        message="Schema transform executed successfully",
    )

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    req = ExecuteSchemaTransformCommand(
        source_file_id="D:/sample.xlsx",
        rules=[{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        file_format="xlsx",
        output_format="csv",
    )

    res = await uc.execute(req)

    assert res.success is True
    assert res.result is not None
    assert res.result.download_url == "http://uploaded.com/result.csv"
    mock_transformer.execute_schema_transform.assert_awaited_once_with(
        "D:/sample.xlsx",
        "xlsx",
        "csv",
        [{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        None,
        None,
        None,
        None,
        None,
        None,
        False,
        False,
        None,
        # derive_field_paths / field_path_overrides: off unless a caller
        # asks, so the workbook is read exactly as it was before.
        False,
        None,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_schema_transform_threads_version_ids(mock_logger):
    mock_transformer = AsyncMock()
    mock_transformer.execute_schema_transform.return_value = SchemaTransformExecutionResult(
        success=True,
        total_rows=3,
        total_columns=2,
        output_schema=FileSchema(
            columns=[SchemaField(name="customer_id", dtype="Int64")],
            sample_data=[{"customer_id": 1}],
            nested_depth=1,
            file_format="csv",
            total_columns=1,
        ),
        preview_data=[{"customer_id": 1}],
        download_url="http://uploaded.com/result.csv",
        output_file_id="file-out",
        output_version_id="ver-out",
        message="Schema transform executed successfully",
    )

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    req = ExecuteSchemaTransformCommand(
        source_file_id="file-source",
        rules=[{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        file_format="csv",
        output_format="csv",
        source_version_id="ver-source",
        target_file_id="file-target",
        target_version_id="ver-target",
    )

    await uc.execute(req)

    mock_transformer.execute_schema_transform.assert_awaited_once_with(
        "file-source",
        "csv",
        "csv",
        [{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
        None,
        None,
        "ver-source",
        "file-target",
        "ver-target",
        None,
        False,
        False,
        None,
        # derive_field_paths / field_path_overrides: off unless a caller
        # asks, so the workbook is read exactly as it was before.
        False,
        None,
    )

@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_schema_transform_failure(mock_logger):
    mock_transformer = AsyncMock()
    mock_transformer.execute_schema_transform.side_effect = Exception("Schema transform failed")

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)

    res = await uc.execute(
        ExecuteSchemaTransformCommand(
            source_file_id="D:/sample.xlsx",
            rules=[{"type": "drop_columns", "params": {"columns": ["tmp"]}}],
            file_format="xlsx",
            output_format="csv",
        )
    )

    assert res.success is False
    assert "Schema transform failed" in res.message

@pytest.mark.unit
def test_query_validation_result(mock_file_reader):
    mock_odata = MagicMock()
    mock_odata.parse_and_format.return_value = {"total_count": 2, "data": [{"col": "val"}]}
    
    # Mock dataframe
    mock_df = MagicMock()
    mock_file_reader.download_and_read.return_value = mock_df

    uc = DataValidationUseCase(MagicMock(), MagicMock(), MagicMock(), mock_file_reader, mock_odata)
    
    result = uc.query_result_file("file-123", "$top=10", version_id="ver-2")
    
    assert result["total_count"] == 2
    mock_file_reader.download_and_read.assert_called_once_with("file-123", "csv", version_id="ver-2")
    mock_odata.parse_and_format.assert_called_once()

# Rule Management Use Case Tests
@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_rule_set_success(mock_logger):
    """Test successful rule set creation"""
    mock_repo = AsyncMock()
    mock_repo.save.return_value = RuleSet(id="test-id", name="Test Rule", rules=[{"type": "required"}])
    
    uc = RuleManagementUseCase(mock_logger, mock_repo)
    command = CreateRuleSetCommand(name="Test Rule", rules=[{"type": "required"}], description="Test description")
    
    result = await uc.create_rule_set(command)
    
    assert result.success is True
    assert result.data.name == "Test Rule"
    assert result.message == "Created successfully"
    mock_repo.save.assert_called_once()
    mock_logger.info.assert_called_once()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_rule_set_success(mock_logger):
    """Test successful rule set update"""
    mock_repo = AsyncMock()
    existing_rule_set = RuleSet(id="test-id", name="Test Rule", rules=[{"type": "required"}])
    mock_repo.find_by_id.return_value = existing_rule_set
    mock_repo.save.return_value = existing_rule_set
    
    uc = RuleManagementUseCase(mock_logger, mock_repo)
    
    result = await uc.update_rule_set("test-id", [{"type": "email"}])
    
    assert result.success is True
    assert result.data.rules == [{"type": "email"}]
    assert result.message == "Updated successfully"
    mock_repo.find_by_id.assert_called_once_with("test-id")
    mock_repo.save.assert_called_once()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_rule_set_not_found(mock_logger):
    """Test rule set update when rule set not found"""
    mock_repo = AsyncMock()
    mock_repo.find_by_id.return_value = None
    
    uc = RuleManagementUseCase(mock_logger, mock_repo)
    
    result = await uc.update_rule_set("non-existent-id", [{"type": "email"}])
    
    assert result.success is False
    assert result.message == "RuleSet not found"
    mock_repo.find_by_id.assert_called_once_with("non-existent-id")
    mock_repo.save.assert_not_called()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_rule_sets_success(mock_logger):
    """Test successful rule sets deletion"""
    mock_repo = AsyncMock()
    
    uc = RuleManagementUseCase(mock_logger, mock_repo)
    
    result = await uc.delete_rule_sets(["id1", "id2", "id3"])
    
    assert result.success is True
    assert result.message == "Deleted successfully"
    assert mock_repo.delete.call_count == 3
    mock_repo.delete.assert_any_call("id1")
    mock_repo.delete.assert_any_call("id2")
    mock_repo.delete.assert_any_call("id3")

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_all_rules_success(mock_logger):
    """Test successful retrieval of all rules"""
    mock_repo = AsyncMock()
    mock_rules = [
        RuleSet(id="1", name="Rule 1", rules=[{"type": "required"}]),
        RuleSet(id="2", name="Rule 2", rules=[{"type": "email"}])
    ]
    mock_repo.find_all.return_value = mock_rules
    
    uc = RuleManagementUseCase(mock_logger, mock_repo)
    
    result = await uc.get_all_rules()
    
    assert result.success is True
    assert len(result.data) == 2
    assert result.data[0].name == "Rule 1"
    assert result.data[1].name == "Rule 2"
    mock_repo.find_all.assert_called_once()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_transform_cloud_data_failure(mock_logger, mock_file_reader, mock_transformer):
    mock_odata_parser = MagicMock()
    mock_transformer.transform_cloud_file.side_effect = Exception("Transform Engine Down")

    uc = DataTransformationUseCase(mock_logger, mock_transformer, mock_file_reader, mock_odata_parser)
    req = DataTransformationCommand(file_id="test.csv", rules=[{"type": "filter", "params": {}}])

    res = await uc.execute(req)

    assert res.success is False
    assert "Transform Engine Down" in res.message
    mock_logger.error.assert_called_once()


# ──────────────────────────────────────────────
# DataValidationUseCase.execute — rule_set_id branch (gap: lines 50-58)
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_by_rule_set_id_success(mock_logger, mock_file_reader, mock_validator):
    """rule_set_id provided and found -> rules loaded from RuleSet.rules and validated."""
    mock_rule_repo = AsyncMock()
    mock_odata_parser = MagicMock()
    mock_rule_repo.find_by_id.return_value = RuleSet(
        id="rs-1",
        name="Loaded Set",
        rules=[{"rule_name": "R-from-set", "type": "required", "params": {}}],
    )
    mock_validator.validate_cloud_file.return_value = ValidationResult(
        total_rows=50, valid_rows=48, invalid_rows=2, odata=ODataContent(data=[])
    )

    uc = DataValidationUseCase(mock_logger, mock_validator, mock_rule_repo, mock_file_reader, mock_odata_parser)
    res = await uc.execute(DataValidationCommand(file_id="test.csv", rule_set_id="rs-1"))

    assert res.success is True
    assert res.result.invalid_rows == 2
    mock_rule_repo.find_by_id.assert_awaited_once_with("rs-1")
    # The rule loaded from the set must have been passed to the validator.
    call = mock_validator.validate_cloud_file.call_args
    domain_rules = call.args[2]
    assert domain_rules[0].rule_name == "R-from-set"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_by_rule_set_id_not_found(mock_logger, mock_file_reader, mock_validator):
    """rule_set_id provided but not found -> success False with '{id} not found' message."""
    mock_rule_repo = AsyncMock()
    mock_odata_parser = MagicMock()
    mock_rule_repo.find_by_id.return_value = None

    uc = DataValidationUseCase(mock_logger, mock_validator, mock_rule_repo, mock_file_reader, mock_odata_parser)
    res = await uc.execute(DataValidationCommand(file_id="test.csv", rule_set_id="missing-id"))

    assert res.success is False
    assert res.message == "RuleSet missing-id not found"
    mock_validator.validate_cloud_file.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_no_rules_and_no_rule_set_id(mock_logger, mock_file_reader, mock_validator):
    """Neither rules nor rule_set_id -> success False with guard message."""
    mock_rule_repo = AsyncMock()
    mock_odata_parser = MagicMock()

    uc = DataValidationUseCase(mock_logger, mock_validator, mock_rule_repo, mock_file_reader, mock_odata_parser)
    res = await uc.execute(DataValidationCommand(file_id="test.csv"))

    assert res.success is False
    assert res.message == "Rules or rule_set_id must be provided"
    mock_validator.validate_cloud_file.assert_not_called()


# ──────────────────────────────────────────────
# DataValidationUseCase.get_download_url / query_result_file fallback (gap: 82-83, 89)
# ──────────────────────────────────────────────

@pytest.mark.unit
def test_validation_get_download_url_delegates_to_storage(mock_file_reader):
    mock_file_reader.generate_presigned_url.return_value = "http://dl/result.csv"
    uc = DataValidationUseCase(MagicMock(), MagicMock(), MagicMock(), mock_file_reader, MagicMock())

    url = uc.get_download_url("result.csv")

    assert url == "http://dl/result.csv"
    mock_file_reader.generate_presigned_url.assert_called_once_with("result.csv", "download", version_id=None)


@pytest.mark.unit
def test_validation_query_result_file_unknown_ext_falls_back_to_csv(mock_file_reader):
    """Unknown extension (.parquet) must fall back to 'csv' when reading."""
    mock_odata = MagicMock()
    mock_odata.parse_and_format.return_value = {"total_count": 0, "data": []}
    mock_file_reader.download_and_read.return_value = MagicMock()

    uc = DataValidationUseCase(MagicMock(), MagicMock(), MagicMock(), mock_file_reader, mock_odata)
    uc.query_result_file("weird.parquet", "$top=5")

    mock_file_reader.download_and_read.assert_called_once_with("weird.parquet", "csv", version_id=None)


@pytest.mark.unit
def test_validation_query_result_file_known_ext_json(mock_file_reader):
    """Known extension (json) is preserved, not overridden to csv."""
    mock_odata = MagicMock()
    mock_odata.parse_and_format.return_value = {"total_count": 1, "data": [{"a": 1}]}
    mock_file_reader.download_and_read.return_value = MagicMock()

    uc = DataValidationUseCase(MagicMock(), MagicMock(), MagicMock(), mock_file_reader, mock_odata)
    uc.query_result_file("out.json", "$top=5")

    mock_file_reader.download_and_read.assert_called_once_with("out.json", "json", version_id=None)


# ──────────────────────────────────────────────
# DataTransformationUseCase.get_download_url / query_result_file (gap: 65, 68-72)
# ──────────────────────────────────────────────

@pytest.mark.unit
def test_transformation_get_download_url_delegates_to_storage(mock_file_reader):
    mock_file_reader.generate_presigned_url.return_value = "http://dl/result.csv"
    uc = DataTransformationUseCase(MagicMock(), MagicMock(), mock_file_reader, MagicMock())

    url = uc.get_download_url("result.csv")

    assert url == "http://dl/result.csv"
    mock_file_reader.generate_presigned_url.assert_called_once_with("result.csv", "download", version_id=None)


@pytest.mark.unit
def test_transformation_query_result_file_known_ext_csv(mock_file_reader):
    mock_odata = MagicMock()
    mock_odata.parse_and_format.return_value = {"total_count": 2, "data": [{"col": "val"}]}
    mock_file_reader.download_and_read.return_value = MagicMock()

    uc = DataTransformationUseCase(MagicMock(), MagicMock(), mock_file_reader, mock_odata)
    result = uc.query_result_file("test.csv", "$top=10")

    assert result["total_count"] == 2
    mock_file_reader.download_and_read.assert_called_once_with("test.csv", "csv", version_id=None)
    mock_odata.parse_and_format.assert_called_once()


@pytest.mark.unit
def test_transformation_query_result_file_unknown_ext_falls_back_to_csv(mock_file_reader):
    """Unknown extension (.txt) must fall back to 'csv'."""
    mock_odata = MagicMock()
    mock_odata.parse_and_format.return_value = {"total_count": 0, "data": []}
    mock_file_reader.download_and_read.return_value = MagicMock()

    uc = DataTransformationUseCase(MagicMock(), MagicMock(), mock_file_reader, mock_odata)
    uc.query_result_file("weird.txt", "$top=5")

    mock_file_reader.download_and_read.assert_called_once_with("weird.txt", "csv", version_id=None)


# ──────────────────────────────────────────────
# SchemaTransformUseCase failure paths (gap: 109-111, 130-132, 151-153)
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_inspect_schema_failure(mock_logger):
    """inspect_file catches a collaborator exception and returns success=False."""
    mock_transformer = MagicMock()
    mock_transformer.inspect_schema.side_effect = Exception("Inspect boom")

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    res = await uc.inspect_file(InspectSchemaCommand(file_path="bad.xlsx", file_format="xlsx"))

    assert res.success is False
    assert "Inspect boom" in res.message
    mock_logger.error.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_mapping_failure(mock_logger):
    """generate_mapping catches a collaborator exception and returns success=False."""
    mock_transformer = MagicMock()
    mock_transformer.generate_mapping.side_effect = Exception("Mapping boom")

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    res = await uc.generate_mapping(
        GenerateSchemaMappingCommand(source_schema={}, target_schema={})
    )

    assert res.success is False
    assert "Mapping boom" in res.message
    mock_logger.error.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preview_transform_failure(mock_logger):
    """preview_transform catches a collaborator exception and returns success=False."""
    mock_transformer = MagicMock()
    mock_transformer.preview_schema_transform.side_effect = Exception("Preview boom")

    uc = SchemaTransformUseCase(mock_logger, mock_transformer)
    res = await uc.preview_transform(
        PreviewSchemaTransformCommand(sample_data=[{"id": 1}], rules=[])
    )

    assert res.success is False
    assert "Preview boom" in res.message
    mock_logger.error.assert_called_once()


# ──────────────────────────────────────────────
# RuleManagementUseCase.get_keywords (gap: lines 53-65)
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_keywords_includes_only_keyworded_active_rules(mock_logger):
    """Active ruleset: only rules with truthy keywords are included; rule_id falls back to rs.id."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-active",
            name="Active",
            status="active",
            rules=[
                {"rule_id": "explicit-id", "rule_name": "KW rule", "keywords": ["price"]},
                {"rule_name": "no-kw rule", "keywords": []},          # skipped (falsy keywords)
                {"rule_name": "fallback rule", "keywords": ["date"]},  # no rule_id -> falls back to rs.id
            ],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    out = await uc.get_keywords()

    assert out["description"] == "Keywords mapping summary."
    rules = out["rules"]
    assert len(rules) == 2
    assert rules[0]["rule_id"] == "explicit-id"
    assert rules[0]["keywords"] == ["price"]
    # rule without rule_id falls back to the ruleset id
    assert rules[1]["rule_id"] == "rs-active"
    assert rules[1]["rule_name"] == "fallback rule"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_keywords_skips_inactive_ruleset(mock_logger):
    """Inactive rulesets are entirely excluded even if they carry keyworded rules."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-inactive",
            name="Inactive",
            status="inactive",
            rules=[{"rule_name": "KW", "keywords": ["price"]}],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    out = await uc.get_keywords()

    assert out["rules"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_keywords_empty_repository(mock_logger):
    """Empty repository -> empty rules list."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = []

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    out = await uc.get_keywords()

    assert out["rules"] == []


# ──────────────────────────────────────────────
# RuleManagementUseCase.find_matching_rules (gap: lines 72-125)
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_matching_rules_template_pl_col_rewrite(mock_logger):
    """Header matches keyword AND rule is a template (pl.col path) ->
    instance appended with rewritten expression + columns and (col) description substituted."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-1",
            name="Templates",
            status="active",
            rules=[{
                "rule_name": "Not null",
                "keywords": ["price"],
                "description": "Check {col} not null",
                "params": {"expression": "pl.col('x').is_not_null()"},  # no 'columns' => template
            }],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    relevant, descriptions = await uc.find_matching_rules(["Unit Price"])

    assert len(relevant) == 1
    inst = relevant[0]
    # columns injected with the real header
    assert inst["params"]["columns"] == ["Unit Price"]
    # pl.col('x') rewritten to reference the real header
    assert "pl.col('Unit Price')" in inst["params"]["expression"]
    # rule_name annotated with the header
    assert inst["rule_name"] == "Not null (Unit Price)"
    # description {col} substituted
    assert descriptions == ["Check Unit Price not null"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_matching_rules_col_literal_path(mock_logger):
    """(col) literal expression path -> replaced with pl.col('<header>')."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-1",
            name="Templates",
            status="active",
            rules=[{
                "rule_name": "Positive",
                "keywords": ["amount"],
                "description": "",
                "params": {"expression": "(col) > 0"},  # (col) literal template
            }],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    relevant, _ = await uc.find_matching_rules(["Total Amount"])

    assert len(relevant) == 1
    assert relevant[0]["params"]["expression"] == "pl.col('Total Amount') > 0"
    assert relevant[0]["params"]["columns"] == ["Total Amount"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_matching_rules_non_template_skipped(mock_logger):
    """Rule that already has 'columns' in params is NOT a template -> skipped."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-1",
            name="Concrete",
            status="active",
            rules=[{
                "rule_name": "Concrete rule",
                "keywords": ["price"],
                "params": {"columns": ["Unit Price"], "expression": "pl.col('x')"},
            }],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    relevant, descriptions = await uc.find_matching_rules(["Unit Price"])

    assert relevant == []
    assert descriptions == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_matching_rules_inactive_ruleset_skipped(mock_logger):
    """Inactive ruleset -> the whole ruleset is skipped."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-1",
            name="Off",
            status="inactive",
            rules=[{
                "rule_name": "Not null",
                "keywords": ["price"],
                "params": {"expression": "(col)"},
            }],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    relevant, descriptions = await uc.find_matching_rules(["Unit Price"])

    assert relevant == []
    assert descriptions == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_matching_rules_keyword_no_header_match(mock_logger):
    """Keyword present but no header contains it -> nothing appended."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-1",
            name="Templates",
            status="active",
            rules=[{
                "rule_name": "Not null",
                "keywords": ["price"],
                "params": {"expression": "(col)"},
            }],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    relevant, descriptions = await uc.find_matching_rules(["Customer Name", "Country"])

    assert relevant == []
    assert descriptions == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_matching_rules_empty_headers_and_no_rules(mock_logger):
    """Empty headers and empty repository -> empty results (no crash)."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = []

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    relevant, descriptions = await uc.find_matching_rules([])

    assert relevant == []
    assert descriptions == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_matching_rules_boundary_substring_keyword_match(mock_logger):
    """BOUNDARY: keyword matches as a normalized substring of the header
    ('qty' inside 'Quantity' -> 'quantity')."""
    mock_repo = AsyncMock()
    mock_repo.find_all.return_value = [
        RuleSet(
            id="rs-1",
            name="Templates",
            status="active",
            rules=[{
                "rule_name": "Positive",
                "keywords": ["quantit"],  # substring of normalized 'quantity'
                "params": {"expression": "(col) >= 0"},
            }],
        ),
    ]

    uc = RuleManagementUseCase(mock_logger, mock_repo)
    relevant, _ = await uc.find_matching_rules(["Quantity"])

    assert len(relevant) == 1
    assert relevant[0]["params"]["columns"] == ["Quantity"]
