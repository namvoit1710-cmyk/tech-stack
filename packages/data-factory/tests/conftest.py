import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from app.layer1_domain.entities.validation import ValidationResult, ODataContent
from app.layer1_domain.entities.transformation import TransformResult
from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import (
    RuleManagementResult,
)
from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import (
    DataTransformationResult,
)
from app.layer2_application.features.data_transformation.use_cases.row_transformation_usecase import (
    RowTransformationResult,
)
from app.layer4_frameworks.providers.transformation.polars_row_transformer_provider import (
    RowTransformResult,
)
from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import (
    DataValidationResult,
)

warnings.filterwarnings(
    "ignore",
    message="datetime.datetime.utcnow.*deprecated",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message="coroutine.*was never awaited",
    category=RuntimeWarning,
)
warnings.filterwarnings(
    "ignore",
    message="'asyncio.iscoroutinefunction' is deprecated",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message="coroutine 'AsyncMockMixin._execute_mock_call' was never awaited",
    category=RuntimeWarning,
)


def create_fastapi_app(container):
    """Simple test app creator since presenters were removed."""
    test_app = app
    test_app.state.container = container
    return test_app


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_file_reader():
    reader = AsyncMock()
    mock_streamer = AsyncMock()
    reader.generate_presigned_url = MagicMock(return_value="http://test-url.com/file.csv")
    reader.download_and_read = MagicMock(return_value=MagicMock())
    reader.upload_dataframe.return_value = MagicMock(
        download_url="http://uploaded.com/result.csv",
        file_id="file-123",
        version_id="ver-456",
    )
    reader.stream_file_from_cloud.return_value = mock_streamer
    return reader


@pytest.fixture
def mock_file_upload():
    uploader = AsyncMock()
    uploader.upload_file_to_s3.return_value = "http://uploaded.com/res.csv"
    uploader.upload_dataframe_to_s3.return_value = "http://uploaded.com/res.csv"
    return uploader


@pytest.fixture
def mock_validator():
    validator = AsyncMock()
    validator.validate_cloud_file.return_value = ValidationResult(
        total_rows=10, valid_rows=10, invalid_rows=0, odata=ODataContent(data=[])
    )
    validator.get_validation_stats.return_value = []
    return validator


@pytest.fixture
def mock_transformer():
    transformer = AsyncMock()
    transformer.transform_cloud_file.return_value = TransformResult(
        success=True, total_rows=10, total_columns=5, odata=None, message="Success"
    )
    return transformer


@pytest.fixture
def mock_rule_repo():
    repo = AsyncMock()
    repo.get_all_raw_rules.return_value = []
    repo.save_raw_rules.return_value = True
    repo.get_all_rules.return_value = []
    repo.save_rules.return_value = True
    repo.find_matching_rules.return_value = ([], [])
    return repo


@pytest.fixture
def mock_val_uc():
    uc = AsyncMock()
    uc.execute.return_value = DataValidationResult(
        success=True,
        message="OK",
        result=ValidationResult(
            total_rows=10,
            valid_rows=10,
            invalid_rows=0,
            odata=ODataContent(data=[]),
        ),
    )
    uc.query_result_file = MagicMock(return_value={"value": [{"id": 1}]})
    uc.get_download_url = MagicMock(
        return_value="http://test-url.com/validation-result.csv"
    )
    return uc


@pytest.fixture
def mock_trans_uc():
    uc = AsyncMock()
    uc.execute.return_value = DataTransformationResult(
        success=True,
        message="OK",
        result=TransformResult(
            success=True,
            total_rows=10,
            total_columns=5,
            odata=None,
            message="Transformed successfully",
        ),
    )
    uc.query_result_file = MagicMock(return_value={"value": [{"id": 1}]})
    uc.get_download_url = MagicMock(return_value="http://test-url.com/result.csv")
    return uc


@pytest.fixture
def mock_row_trans_uc():
    uc = AsyncMock()
    uc.execute.return_value = RowTransformationResult(
        success=True,
        message="Rows transformed",
        result=RowTransformResult(
            success=True,
            affected_rows=2,
            total_rows=8,
            message="Deleted 2 rows",
            preview_data=[{"id": 1}],
            download_url="http://test-url.com/result.csv",
        ),
    )
    return uc


@pytest.fixture
def mock_rule_uc(mock_rule_repo):
    uc = AsyncMock()
    uc.get_all_rules.return_value = RuleManagementResult(
        success=True, data=[], message="OK"
    )
    uc.create_rule_set.return_value = RuleManagementResult(
        success=True,
        data={"id": "new-id", "name": "Test", "rules": []},
        message="Created",
    )
    uc.save_rules.return_value = True
    uc.find_matching_rules.return_value = ([], [])
    return uc


@pytest.fixture
def test_app(mock_val_uc, mock_trans_uc, mock_row_trans_uc, mock_rule_uc):
    """Creates a FastAPI test client with mocked Use Cases."""
    container = {
        "data_validation_usecase": mock_val_uc,
        "data_transformation_usecase": mock_trans_uc,
        "row_transformation_usecase": mock_row_trans_uc,
        "rule_management_usecase": mock_rule_uc,
    }
    app_instance = create_fastapi_app(container)
    return TestClient(app_instance)
