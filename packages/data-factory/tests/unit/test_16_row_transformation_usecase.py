"""
Unit tests for RowTransformationUseCase.

Covers the operation validation logic, all identifier types,
insert/delete operations, and error handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.layer2_application.features.data_transformation.use_cases.row_transformation_usecase import (
    RowTransformationUseCase,
    RowTransformationCommand,
    RowTransformationResult,
)
from app.layer4_frameworks.providers.transformation.polars_row_transformer_provider import (
    RowTransformResult,
    PolarsRowTransformerProvider,
)


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.generate_presigned_url = MagicMock(return_value="http://test.com/file.csv")
    return storage


@pytest.fixture
def usecase(mock_logger, mock_storage):
    row_transformer = PolarsRowTransformerProvider(mock_logger, mock_storage)
    return RowTransformationUseCase(
        logger=mock_logger, storage=mock_storage, row_transformer=row_transformer
    )


# ──────────────────────────────────────────────
# Delete Operations
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_by_index(usecase):
    """Delete a row by index."""
    with patch.object(usecase.row_transformer, "transform_rows", new_callable=AsyncMock) as mock_transform:
        mock_transform.return_value = RowTransformResult(
            success=True, affected_rows=1, total_rows=9, message="OK"
        )
        cmd = RowTransformationCommand(
            file_path="test.csv",
            operations=[{
                "operation": "delete",
                "row_identifier": {"type": "index", "index": 3},
            }],
        )
        result = await usecase.execute(cmd)

        assert result.success is True
        assert result.result.affected_rows == 1
        mock_transform.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_by_condition(usecase):
    """Delete rows matching a condition expression."""
    with patch.object(usecase.row_transformer, "transform_rows", new_callable=AsyncMock) as mock_transform:
        mock_transform.return_value = RowTransformResult(
            success=True, affected_rows=3, total_rows=7, message="Deleted 3"
        )
        cmd = RowTransformationCommand(
            file_path="test.csv",
            operations=[{
                "operation": "delete",
                "row_identifier": {"type": "condition", "expression": "pl.col('age') < 18"},
            }],
        )
        result = await usecase.execute(cmd)

        assert result.success is True
        assert result.result.affected_rows == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_by_values(usecase):
    """Delete rows matching exact values."""
    with patch.object(usecase.row_transformer, "transform_rows", new_callable=AsyncMock) as mock_transform:
        mock_transform.return_value = RowTransformResult(
            success=True, affected_rows=1, total_rows=9, message="OK"
        )
        cmd = RowTransformationCommand(
            file_path="test.csv",
            operations=[{
                "operation": "delete",
                "row_identifier": {"type": "values", "match_values": {"id": "42", "name": "John"}},
            }],
        )
        result = await usecase.execute(cmd)

        assert result.success is True


# ──────────────────────────────────────────────
# Insert Operations
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_insert_row(usecase):
    """Insert a row with before position."""
    with patch.object(usecase.row_transformer, "transform_rows", new_callable=AsyncMock) as mock_transform:
        mock_transform.return_value = RowTransformResult(
            success=True, affected_rows=1, total_rows=11, message="Inserted 1"
        )
        cmd = RowTransformationCommand(
            file_path="test.csv",
            operations=[{
                "operation": "insert",
                "row_identifier": {"type": "index", "index": 0},
                "data": {"id": "99", "name": "New"},
                "position": "before",
            }],
        )
        result = await usecase.execute(cmd)

        assert result.success is True
        assert result.result.affected_rows == 1


# ──────────────────────────────────────────────
# Update Operations
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_row(usecase):
    """Update a row by values identifier."""
    with patch.object(usecase.row_transformer, "transform_rows", new_callable=AsyncMock) as mock_transform:
        mock_transform.return_value = RowTransformResult(
            success=True, affected_rows=1, total_rows=10, message="Updated 1"
        )
        cmd = RowTransformationCommand(
            file_path="test.csv",
            operations=[{
                "operation": "update",
                "row_identifier": {"type": "values", "match_values": {"id": "99"}},
                "data": {"name": "Updated"},
            }],
        )
        result = await usecase.execute(cmd)

        assert result.success is True
        assert result.result.affected_rows == 1


# ──────────────────────────────────────────────
# Validation Errors
# ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_operation_type(usecase):
    """Invalid operation type returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "merge",
            "row_identifier": {"type": "index", "index": 0},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "Invalid operation type" in result.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_missing_data(usecase):
    """Update without data returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "update",
            "row_identifier": {"type": "index", "index": 0},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "data" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_rejects_position(usecase):
    """Update should not accept position field."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "update",
            "row_identifier": {"type": "index", "index": 0},
            "data": {"name": "Updated"},
            "position": "before",
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "position is not supported" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_rejects_non_boolean_allow_multiple(usecase):
    """Update allow_multiple must be boolean."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "update",
            "row_identifier": {"type": "index", "index": 0},
            "data": {"name": "Updated"},
            "allow_multiple": "yes",
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "allow_multiple" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_row_identifier(usecase):
    """Missing row_identifier returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{"operation": "delete"}],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "row_identifier" in result.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_identifier_type(usecase):
    """Missing identifier type returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "delete",
            "row_identifier": {"type": None},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "type" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_identifier_type(usecase):
    """Invalid identifier type returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "delete",
            "row_identifier": {"type": "regex"},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "Invalid identifier_type" in result.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_index_type_missing_index(usecase):
    """Index type without index value returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "delete",
            "row_identifier": {"type": "index"},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "index" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_index_type_invalid_index(usecase):
    """Index type with non-integer index returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "delete",
            "row_identifier": {"type": "index", "index": "abc"},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "integer" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_condition_type_missing_expression(usecase):
    """Condition type without expression returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "delete",
            "row_identifier": {"type": "condition"},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "expression" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_values_type_missing_match_values(usecase):
    """Values type without match_values returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "delete",
            "row_identifier": {"type": "values"},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "match_values" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_insert_missing_data(usecase):
    """Insert without data returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "insert",
            "row_identifier": {"type": "index", "index": 0},
            "position": "before",
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "data" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_insert_missing_position(usecase):
    """Insert without position returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "insert",
            "row_identifier": {"type": "index", "index": 0},
            "data": {"id": "1"},
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "position" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_insert_invalid_position(usecase):
    """Insert with invalid position string returns error."""
    cmd = RowTransformationCommand(
        file_path="test.csv",
        operations=[{
            "operation": "insert",
            "row_identifier": {"type": "index", "index": 0},
            "data": {"id": "1"},
            "position": "middle",
        }],
    )
    result = await usecase.execute(cmd)
    assert result.success is False
    assert "position" in result.message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_operations(usecase):
    """Multiple operations in one command."""
    with patch.object(usecase.row_transformer, "transform_rows", new_callable=AsyncMock) as mock_transform:
        mock_transform.return_value = RowTransformResult(
            success=True, affected_rows=2, total_rows=8, message="OK"
        )
        cmd = RowTransformationCommand(
            file_path="test.csv",
            operations=[
                {
                    "operation": "delete",
                    "row_identifier": {"type": "index", "index": 0},
                },
                {
                    "operation": "delete",
                    "row_identifier": {"type": "condition", "expression": "pl.col('age') < 0"},
                },
            ],
        )
        result = await usecase.execute(cmd)
        assert result.success is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transformer_exception(usecase):
    """Exception in transformer is caught and returned as failure."""
    with patch.object(usecase.row_transformer, "transform_rows", new_callable=AsyncMock) as mock_transform:
        mock_transform.side_effect = Exception("S3 connection failed")
        cmd = RowTransformationCommand(
            file_path="test.csv",
            operations=[{
                "operation": "delete",
                "row_identifier": {"type": "index", "index": 0},
            }],
        )
        result = await usecase.execute(cmd)
        assert result.success is False
        assert "S3 connection failed" in result.message


@pytest.mark.unit
def test_get_download_url(usecase, mock_storage):
    """get_download_url delegates to storage."""
    url = usecase.get_download_url("result.csv")
    assert url == "http://test.com/file.csv"
    mock_storage.generate_presigned_url.assert_called_once_with("result.csv", "download")
