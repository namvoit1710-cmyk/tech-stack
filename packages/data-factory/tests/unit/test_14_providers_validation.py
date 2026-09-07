import pytest
import polars as pl
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from unittest.mock import patch
from app.layer4_frameworks.providers.validation.polars_validator_provider import PolarsValidatorProvider, _StreamingResultWriter
from app.layer1_domain.entities.validation import ValidationRule, ValidationResult, ODataContent, ODataData
import warnings

# Suppress the specific AsyncMock warning for this test file
warnings.filterwarnings("ignore", message="coroutine 'AsyncMockMixin._execute_mock_call' was never awaited", category=RuntimeWarning)


@pytest.mark.unit
class TestPolarsValidatorProvider:
    def setup_method(self):
        self.logger_mock = MagicMock()
        self.storage_mock = MagicMock()
        self.provider = PolarsValidatorProvider(self.logger_mock, self.storage_mock)

    @pytest.mark.asyncio
    async def test_validate_cloud_file_success(self):
        """Test successful validation with no errors"""
        # Mock dataframe
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "email"]
        mock_df.with_columns.return_value = mock_df
        mock_df.select.return_value = mock_df
        mock_df.group_by.return_value = mock_df
        mock_df.agg.return_value = mock_df
        mock_df.filter.return_value = mock_df
        mock_df.to_dicts.return_value = []

        self.storage_mock.download_and_read.return_value = mock_df

        rules = [ValidationRule(
            rule_name="Required ID",
            type="required",
            params={"columns": ["id"]},
            error_message="ID is required",
            description="Check required ID field"
        )]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.invalid_rows == 0
        assert result.total_rows == 100
        assert result.valid_rows == 100
        self.storage_mock.download_and_read.assert_called_once_with(
            "test.csv", "csv", version_id=None, sheet_names=None, header_row=None
        )
        self.logger_mock.info.assert_called()

    @pytest.mark.asyncio
    async def test_validate_cloud_file_passes_version_id_to_storage(self):
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=10)
        mock_df.columns = ["id"]
        self.storage_mock.download_and_read.return_value = mock_df

        rules = [ValidationRule(
            rule_name="Required ID",
            type="required",
            params={"columns": ["id"]},
            error_message="ID is required",
            description="Check required ID field"
        )]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules, version_id="ver-1")

        assert result.total_rows == 10
        self.storage_mock.download_and_read.assert_called_once_with(
            "test.csv", "csv", version_id="ver-1", sheet_names=None, header_row=None
        )

    @pytest.mark.asyncio
    async def test_validate_cloud_file_with_errors(self):
        """Test validation with errors found"""
        # Mock dataframe with errors
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "email"]

        # Mock the validation process
        mock_df.with_columns.return_value = mock_df
        mock_df.select.return_value = mock_df

        # Mock error data
        error_df_mock = MagicMock()
        error_df_mock.to_dicts.return_value = [
            {"id": 1, "err_0": True, "rule_name": "Required ID", "error_message": "ID is required"}
        ]
        mock_df.filter.return_value = error_df_mock

        self.storage_mock.download_and_read.return_value = mock_df

        rules = [ValidationRule(
            rule_name="Required ID",
            type="required",
            params={"columns": ["id"]},
            error_message="ID is required",
            description="Check required ID field"
        )]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.invalid_rows == 0
        assert result.total_rows == 100
        assert isinstance(result.odata, ODataContent)

    @pytest.mark.asyncio
    async def test_validate_cloud_file_unique_rule(self):
        """Test validation with unique constraint rule"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "email"]

        # Mock unique validation
        mock_df.group_by.return_value = mock_df
        mock_df.agg.return_value = mock_df
        mock_df.filter.return_value = mock_df
        mock_df.with_columns.return_value = mock_df
        mock_df.select.return_value = mock_df

        # Mock no duplicates found
        mock_df.to_dicts.return_value = []

        self.storage_mock.download_and_read.return_value = mock_df

        rules = [ValidationRule(
            rule_name="Unique Email",
            type="unique",
            params={"columns": ["email"]},
            error_message="Email must be unique",
            description="Check email uniqueness"
        )]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.invalid_rows == 0
        assert result.total_rows == 100

    @pytest.mark.asyncio
    async def test_validate_cloud_file_multiple_rules(self):
        """Test validation with multiple rules"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "email", "age"]

        mock_df.with_columns.return_value = mock_df
        mock_df.select.return_value = mock_df
        mock_df.filter.return_value = mock_df
        mock_df.to_dicts.return_value = []

        self.storage_mock.download_and_read.return_value = mock_df

        rules = [
            ValidationRule(
                rule_name="Required ID",
                type="required",
                params={"columns": ["id"]},
                error_message="ID is required"
            ),
            ValidationRule(
                rule_name="Email Format",
                type="email",
                params={"columns": ["email"]},
                error_message="Invalid email format"
            ),
            ValidationRule(
                rule_name="Age Range",
                type="range",
                params={"columns": ["age"], "min": 18, "max": 100},
                error_message="Age must be between 18 and 100"
            )
        ]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.total_rows == 100
        self.logger_mock.info.assert_called()

    @pytest.mark.asyncio
    async def test_validate_cloud_file_download_error(self):
        """Test validation with download error"""
        self.storage_mock.download_and_read.side_effect = Exception("Download failed")

        rules = [ValidationRule(
            rule_name="Test Rule",
            type="required",
            params={"columns": ["id"]},
            error_message="Required field"
        )]

        with pytest.raises(Exception) as exc_info:
            await self.provider.validate_cloud_file("test.csv", "csv", rules)
        
        assert "Download failed" in str(exc_info.value)
        self.logger_mock.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_cloud_file_empty_rules(self):
        """Test validation with no rules"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=50)
        mock_df.columns = ["id", "name"]

        self.storage_mock.download_and_read.return_value = mock_df

        result = await self.provider.validate_cloud_file("test.csv", "csv", [])

        assert result.total_rows == 50
        assert result.valid_rows == 50
        assert result.invalid_rows == 0

    @pytest.mark.asyncio
    async def test_validate_cloud_file_different_formats(self):
        """Test validation with different file formats"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=75)
        mock_df.columns = ["id", "name"]
        mock_df.with_columns.return_value = mock_df
        mock_df.select.return_value = mock_df
        mock_df.filter.return_value = mock_df
        mock_df.to_dicts.return_value = []

        self.storage_mock.download_and_read.return_value = mock_df

        rules = [ValidationRule(
            rule_name="Required Name",
            type="required",
            params={"columns": ["name"]},
            error_message="Name is required"
        )]

        # Test JSON format
        result = await self.provider.validate_cloud_file("test.json", "json", rules)
        assert result.invalid_rows == 0
        self.storage_mock.download_and_read.assert_called_with(
            "test.json", "json", version_id=None, sheet_names=None, header_row=None
        )

        # Reset mock
        self.storage_mock.reset_mock()
        self.storage_mock.download_and_read.return_value = mock_df

        # Test Excel format
        result = await self.provider.validate_cloud_file("test.xlsx", "xlsx", rules)
        assert result.invalid_rows == 0
        self.storage_mock.download_and_read.assert_called_with(
            "test.xlsx", "xlsx", version_id=None, sheet_names=None, header_row=None
        )

    @pytest.mark.asyncio
    async def test_validate_cloud_file_set_unique_detects_duplicate_composite_keys(self):
        self.storage_mock._get_file_metadata.return_value = {"filename": "customers.csv"}
        self.storage_mock._extract_source_filename.return_value = "customers.csv"
        self.storage_mock.download_and_read.return_value = pl.DataFrame({
            "first_name": ["John", "Jane", "John"],
            "last_name": ["Doe", "Doe", "Doe"],
        })
        self.storage_mock.upload_file.return_value = SimpleNamespace(
            download_url="http://test.com/result.csv",
            file_id="result-file",
            version_id="v1",
        )

        rules = [ValidationRule(
            rule_name="Unique Full Name",
            type="set_unique",
            params={"columns": ["first_name", "last_name"]},
            error_message="Full name must be unique",
        )]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.total_rows == 3
        assert result.invalid_rows == 2
        assert result.valid_rows == 1
        assert len(result.odata.data) == 1
        assert result.odata.data[0].rule_name == "Unique Full Name"
        assert result.odata.data[0].violate == 2
        assert next(iter(result.odata.data[0].error_code.values())) == ["first_name", "last_name"]
        self.storage_mock.upload_file.assert_called_once()
        assert self.storage_mock.upload_file.call_args.kwargs["filename"] == "edited_validation_datafactory.csv"

    @pytest.mark.asyncio
    async def test_validate_cloud_file_set_unique_handles_null_keys_as_values(self):
        self.storage_mock.download_and_read.return_value = pl.DataFrame({
            "first_name": [None, None, "Jane"],
            "last_name": ["Doe", "Doe", "Doe"],
        })
        self.storage_mock.upload_file.return_value = SimpleNamespace(
            download_url="http://test.com/result.csv",
            file_id="result-file",
            version_id=None,
        )

        rules = [ValidationRule(
            rule_name="Unique Full Name",
            type="set_unique",
            params={"columns": ["first_name", "last_name"]},
            error_message="Full name must be unique",
        )]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.invalid_rows == 2
        assert result.odata.data[0].violate == 2

    @pytest.mark.asyncio
    async def test_validate_cloud_file_set_unique_detects_duplicates_across_batches(self):
        self.storage_mock.download_and_read.return_value = pl.DataFrame({
            "first_name": ["John", "Jane", "Alice", "John"],
            "last_name": ["Doe", "Roe", "Smith", "Doe"],
        })
        self.storage_mock.upload_file.return_value = SimpleNamespace(
            download_url="http://test.com/result.csv",
            file_id="result-file",
            version_id=None,
        )

        rules = [ValidationRule(
            rule_name="Unique Full Name",
            type="set_unique",
            params={"columns": ["first_name", "last_name"]},
            error_message="Full name must be unique",
        )]

        with patch(
            "app.layer4_frameworks.providers.validation.polars_validator_provider.resolve_adaptive_batch_size",
            return_value=2,
        ):
            result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.invalid_rows == 2
        assert result.valid_rows == 2
        assert result.odata.data[0].violate == 2

    @pytest.mark.asyncio
    async def test_validate_cloud_file_uses_adaptive_batching_for_row_rules(self):
        self.storage_mock.download_and_read.return_value = pl.DataFrame(
            {
                "id": ["1", None, "3", None],
                "name": ["A", "B", "C", "D"],
            }
        )
        self.storage_mock.upload_file.return_value = SimpleNamespace(
            download_url="http://test.com/result.csv",
            file_id="result-file",
            version_id=None,
        )

        rules = [
            ValidationRule(
                rule_name="Required ID",
                type="required",
                params={"columns": ["id"]},
                error_message="ID is required",
            )
        ]

        with patch(
            "app.layer4_frameworks.providers.validation.polars_validator_provider.resolve_adaptive_batch_size",
            return_value=2,
        ) as batch_size_mock:
            result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.invalid_rows == 2
        batch_size_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_cloud_file_set_unique_rejects_missing_dataframe_columns(self):
        self.storage_mock.download_and_read.return_value = pl.DataFrame({
            "first_name": ["John", "Jane"],
        })

        rules = [ValidationRule(
            rule_name="Unique Full Name",
            type="set_unique",
            params={"columns": ["first_name", "last_name"]},
            error_message="Full name must be unique",
        )]

        with pytest.raises(ValueError, match="references missing columns"):
            await self.provider.validate_cloud_file("test.csv", "csv", rules)

    @pytest.mark.asyncio
    async def test_validate_cloud_file_set_unique_rejects_missing_columns_param(self):
        self.storage_mock.download_and_read.return_value = pl.DataFrame({
            "first_name": ["John", "Jane"],
            "last_name": ["Doe", "Roe"],
        })

        rules = [ValidationRule(
            rule_name="Unique Full Name",
            type="set_unique",
            params={},
            error_message="Full name must be unique",
        )]

        with pytest.raises(ValueError, match="params.columns as a non-empty list"):
            await self.provider.validate_cloud_file("test.csv", "csv", rules)

    @pytest.mark.asyncio
    async def test_validate_cloud_file_set_unique_rejects_non_list_columns_param(self):
        self.storage_mock.download_and_read.return_value = pl.DataFrame({
            "first_name": ["John", "Jane"],
            "last_name": ["Doe", "Roe"],
        })

        rules = [ValidationRule(
            rule_name="Unique Full Name",
            type="set_unique",
            params={"columns": "first_name"},
            error_message="Full name must be unique",
        )]

        with pytest.raises(ValueError, match="params.columns as a non-empty list"):
            await self.provider.validate_cloud_file("test.csv", "csv", rules)

    @pytest.mark.asyncio
    async def test_validate_cloud_file_set_unique_coexists_with_row_rules(self):
        self.storage_mock.download_and_read.return_value = pl.DataFrame({
            "id": ["1", None],
            "first_name": ["John", "John"],
            "last_name": ["Doe", "Doe"],
        })
        self.storage_mock.upload_file.return_value = SimpleNamespace(
            download_url="http://test.com/result.csv",
            file_id="result-file",
            version_id=None,
        )

        rules = [
            ValidationRule(
                rule_name="Required ID",
                type="required",
                params={"columns": ["id"]},
                error_message="ID is required",
            ),
            ValidationRule(
                rule_name="Unique Full Name",
                type="set_unique",
                params={"columns": ["first_name", "last_name"]},
                error_message="Full name must be unique",
            ),
        ]

        result = await self.provider.validate_cloud_file("test.csv", "csv", rules)

        assert result.invalid_rows == 2
        assert sorted(item.rule_name for item in result.odata.data) == ["Required ID", "Unique Full Name"]

    def test_streaming_result_writer_writes_only_invalid_rows(self):
        writer = _StreamingResultWriter(self.logger_mock)
        try:
            error_struct = pl.Struct({
                "rule": pl.String,
                "field": pl.String,
                "message": pl.String,
                "value": pl.String,
            })
            df = pl.DataFrame({
                "id": [1, 2],
                "err_0": [
                    {"rule": "Required ID", "field": "id", "message": "ID is required", "value": ""},
                    None,
                ],
            }, schema={"id": pl.Int64, "err_0": error_struct})

            writer.write(df, ["err_0"])

            written_df = pl.read_csv(writer.file_path)
            assert written_df.height == 1
            assert written_df["id"].to_list() == [1]
            assert written_df["has_validation_error"].to_list() == [True]
        finally:
            writer.cleanup()

    def test_streaming_result_writer_writes_full_file_when_requested(self):
        writer = _StreamingResultWriter(self.logger_mock, result_mode="full_file")
        try:
            error_struct = pl.Struct({
                "rule": pl.String,
                "field": pl.String,
                "message": pl.String,
                "value": pl.String,
            })
            df = pl.DataFrame({
                "id": [1, 2],
                "err_0": [
                    {"rule": "Required ID", "field": "id", "message": "ID is required", "value": ""},
                    None,
                ],
            }, schema={"id": pl.Int64, "err_0": error_struct})

            writer.write(df, ["err_0"])

            written_df = pl.read_csv(writer.file_path)
            assert written_df.height == 2
            assert written_df["id"].to_list() == [1, 2]
            assert written_df["has_validation_error"].to_list() == [True, False]
        finally:
            writer.cleanup()