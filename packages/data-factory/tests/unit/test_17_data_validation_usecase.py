import pytest
from unittest.mock import AsyncMock, MagicMock

from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import (
    DataValidationCommand,
    DataValidationUseCase,
)


@pytest.fixture
def usecase():
    logger = MagicMock()
    validator = AsyncMock()
    rule_repo = AsyncMock()
    storage = MagicMock()
    odata_parser = MagicMock()
    return DataValidationUseCase(
        logger=logger,
        validator=validator,
        rule_repo=rule_repo,
        storage=storage,
        odata_parser=odata_parser,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_returns_failed_result_when_validator_rejects_set_unique_rule(usecase):
    usecase.validator.validate_cloud_file.side_effect = ValueError(
        "Rule 'Unique Full Name' must define params.columns as a non-empty list"
    )

    command = DataValidationCommand(
        file_id="test.csv",
        file_format="csv",
        rules=[{
            "rule_name": "Unique Full Name",
            "type": "set_unique",
            "params": {},
            "error_message": "Full name must be unique",
        }],
    )

    result = await usecase.execute(command)

    assert result.success is False
    assert "params.columns" in result.message
    usecase.validator.validate_cloud_file.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_passes_full_file_result_mode_to_validator(usecase):
    usecase.validator.validate_cloud_file.return_value = MagicMock(
        invalid_rows=0,
        total_rows=10,
    )

    command = DataValidationCommand(
        file_id="test.csv",
        file_format="csv",
        result_mode="full_file",
        rules=[{
            "rule_name": "Unique ID",
            "type": "unique",
            "params": {"columns": ["id"]},
            "error_message": "Duplicate id found",
        }],
    )

    result = await usecase.execute(command)

    assert result.success is True
    assert usecase.validator.validate_cloud_file.await_args.kwargs["result_mode"] == "full_file"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_rejects_invalid_result_mode(usecase):
    command = DataValidationCommand(
        file_id="test.csv",
        file_format="csv",
        result_mode="everything",
        rules=[{
            "rule_name": "Unique ID",
            "type": "unique",
            "params": {"columns": ["id"]},
            "error_message": "Duplicate id found",
        }],
    )

    result = await usecase.execute(command)

    assert result.success is False
    assert "result_mode" in result.message
    usecase.validator.validate_cloud_file.assert_not_awaited()