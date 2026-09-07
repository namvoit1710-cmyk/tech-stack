import pytest
import polars as pl
from unittest.mock import MagicMock, AsyncMock, patch
from app.layer4_frameworks.providers.transformation.polars_transformer_provider import PolarsTransformerProvider
from app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider import PolarsSchemaTransformerProvider
from app.layer4_frameworks.providers.transformation.polars_row_transformer_provider import PolarsRowTransformerProvider, RowOperation, RowIdentifier
from app.layer1_domain.entities.transformation import TransformRule, TransformResult
from app.layer1_domain.entities.schema_transform import (
    FileSchema,
    SchemaMappingResult,
    FieldMappingSuggestion,
)


@pytest.mark.unit
class TestPolarsTransformerProvider:
    def setup_method(self):
        self.logger_mock = MagicMock()
        self.storage_mock = MagicMock()
        self.provider = PolarsTransformerProvider(self.logger_mock, self.storage_mock)

    @pytest.mark.asyncio
    async def test_transform_cloud_file_filter_rule(self):
        """Test transformation with filter rule"""
        # Mock dataframe
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status"]
        mock_df.filter.return_value = mock_df
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test"}]
        mock_df.__len__ = MagicMock(return_value=50)  # After filtering

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        rules = [TransformRule(
            rule_name="Filter Active",
            type="filter",
            params={"expression": "pl.col('status') == 'active'"},
            description="Filter active records"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        assert result.total_rows == 50
        assert result.total_columns == 3
        assert "Transformed successfully" in result.message
        self.storage_mock.download_and_read.assert_called_once_with(
            "test.csv",
            "csv",
            version_id=None,
        )
        self.storage_mock.upload_dataframe.assert_called_once()
        mock_df.filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_mapping_rule(self):
        """Test transformation with mapping rule"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name"]
        mock_df.with_columns.return_value = mock_df
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test", "upper_name": "TEST"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        rules = [TransformRule(
            rule_name="Upper Case Name",
            type="mapping",
            params={"expression": "pl.col('name').str.to_uppercase()", "new_col": "upper_name"},
            description="Convert name to uppercase"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        assert result.total_rows == 100
        self.storage_mock.upload_dataframe.assert_called_once()
        mock_df.with_columns.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_drop_columns(self):
        """Test transformation with drop columns rule"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status", "temp"]
        mock_df.drop.return_value = mock_df
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test", "status": "active"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        rules = [TransformRule(
            rule_name="Drop Temp Column",
            type="drop_columns",
            params={"columns_to_drop": ["temp"]},
            description="Remove temporary column"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        mock_df.drop.assert_called_once_with(["temp"])

    @pytest.mark.asyncio
    async def test_transform_cloud_file_rename_columns(self):
        """Test transformation with rename columns rule"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "old_name"]
        mock_df.rename.return_value = mock_df
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "new_name": "test"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        rules = [TransformRule(
            rule_name="Rename Column",
            type="rename_columns",
            params={"mapping": {"old_name": "new_name"}},
            description="Rename old_name to new_name"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        mock_df.rename.assert_called_once_with({"old_name": "new_name"})

    @pytest.mark.asyncio
    async def test_transform_cloud_file_case_when_expr(self):
        """Test transformation with case when expression"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "score"]
        mock_df.with_columns.return_value = mock_df
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "score": 85, "grade": "B"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        rules = [TransformRule(
            rule_name="Grade Assignment",
            type="case_when_expr",
            params={
                "column_dest": "grade",
                "conditions": [
                    {"when_expression": "pl.col('score') >= 90", "result": "A"},
                    {"when_expression": "pl.col('score') >= 80", "result": "B"}
                ],
                "otherwise": "C"
            },
            description="Assign grades based on score"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        assert result.total_rows == 100

    @pytest.mark.asyncio
    async def test_transform_cloud_file_batches_safe_rules(self):
        source_df = pl.DataFrame(
            {
                "id": [1, 2, 3, 4],
                "name": ["alpha", "beta", "gamma", "delta"],
                "status": ["active", "inactive", "active", "active"],
            }
        )
        self.storage_mock.download_and_read.return_value = source_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        rules = [
            TransformRule(
                rule_name="Filter Active",
                type="filter",
                params={"expression": "pl.col('status') == 'active'"},
            ),
            TransformRule(
                rule_name="Upper Case Name",
                type="mapping",
                params={"expression": "pl.col('name').str.to_uppercase()", "new_col": "upper_name"},
            ),
        ]

        with patch(
            "app.layer4_frameworks.providers.transformation.polars_transformer_provider.resolve_adaptive_batch_size",
            return_value=2,
        ):
            result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        uploaded_df = self.storage_mock.upload_dataframe.call_args.args[0]
        assert uploaded_df.to_dicts() == [
            {"id": 1, "name": "alpha", "status": "active", "upper_name": "ALPHA"},
            {"id": 3, "name": "gamma", "status": "active", "upper_name": "GAMMA"},
            {"id": 4, "name": "delta", "status": "active", "upper_name": "DELTA"},
        ]

    def test_transform_cloud_file_avoids_batching_for_global_expressions(self):
        rules = [
            TransformRule(
                rule_name="Global Rank",
                type="mapping",
                params={"expression": "pl.col('score').rank()", "new_col": "score_rank"},
            )
        ]

        assert self.provider._can_use_batched_execution(rules) is False

    @pytest.mark.asyncio
    async def test_transform_cloud_file_error_handling(self):
        """Test transformation error handling"""
        self.storage_mock.download_and_read.side_effect = Exception("Download failed")

        rules = [TransformRule(
            rule_name="Test Rule",
            type="filter",
            params={"expression": "pl.col('status') == 'active'"},
            description="Test rule"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is False
        assert "Download failed" in result.message
        self.logger_mock.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_rule_error(self):
        """Test transformation with invalid rule expression"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name"]
        mock_df.filter.side_effect = Exception("Invalid expression")
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        rules = [TransformRule(
            rule_name="Invalid Rule",
            type="filter",
            params={"expression": "invalid_expression()"},
            description="Invalid rule"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True  # Should continue despite rule error
        self.logger_mock.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_insert_row_by_index(self):
        """Test row insertion by index"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status"]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        # Mock the row transformer
        self.provider.row_transformer = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.affected_rows = 1
        self.provider.row_transformer.transform_rows.return_value = mock_result

        rules = [TransformRule(
            rule_name="Insert New Row",
            type="insert_row",
            params={
                "identifier_type": "index",
                "index": 5,
                "data": {"id": 999, "name": "New User", "status": "active"},
                "position": "after"
            },
            description="Insert a new row after index 5"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        self.provider.row_transformer.transform_rows.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_delete_row_by_condition(self):
        """Test row deletion by condition"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status"]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        # Mock the row transformer
        self.provider.row_transformer = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.affected_rows = 1
        self.provider.row_transformer.transform_rows.return_value = mock_result

        rules = [TransformRule(
            rule_name="Delete Inactive Users",
            type="delete_row",
            params={
                "identifier_type": "condition",
                "expression": "pl.col('status') == 'inactive'"
            },
            description="Delete all inactive users"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        self.provider.row_transformer.transform_rows.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_delete_row_by_values(self):
        """Test row deletion by exact value matching"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status"]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        # Mock the row transformer
        self.provider.row_transformer = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.affected_rows = 1
        self.provider.row_transformer.transform_rows.return_value = mock_result

        rules = [TransformRule(
            rule_name="Delete Specific User",
            type="delete_row",
            params={
                "identifier_type": "values",
                "match_values": {"id": 123, "name": "John Doe"}
            },
            description="Delete user with specific ID and name"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        self.provider.row_transformer.transform_rows.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_insert_row_with_column_indices(self):
        """Test row insertion using column indices instead of names"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status"]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        # Mock the row transformer
        self.provider.row_transformer = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.affected_rows = 1
        self.provider.row_transformer.transform_rows.return_value = mock_result

        rules = [TransformRule(
            rule_name="Insert Row by Index",
            type="insert_row",
            params={
                "identifier_type": "index",
                "index": 0,
                "data": {"0": 999, "1": "New User", "2": "active"},  # Using column indices
                "position": "before",
                "use_column_indices": True
            },
            description="Insert a new row at the beginning using column indices"
        )]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        # Verify that transform_rows was called with operations containing use_column_indices=True
        call_args = self.provider.row_transformer.transform_rows.call_args
        operations = call_args[0][2]  # operations is the 3rd positional argument
        assert len(operations) == 1
        assert operations[0].use_column_indices == True

    @pytest.mark.asyncio
    async def test_transform_cloud_file_batches_contiguous_row_operations(self):
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status"]
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "A", "status": "active"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        self.provider.row_transformer = MagicMock()
        row_result = MagicMock()
        row_result.success = True
        row_result.affected_rows = 2
        self.provider.row_transformer.transform_rows = AsyncMock(return_value=row_result)

        rules = [
            TransformRule(
                rule_name="Insert row",
                type="insert_row",
                params={
                    "identifier_type": "index",
                    "index": 0,
                    "data": {"id": 999, "name": "X", "status": "active"},
                    "position": "after",
                },
            ),
            TransformRule(
                rule_name="Delete by condition",
                type="delete_row",
                params={
                    "identifier_type": "condition",
                    "expression": "pl.col('status') == 'inactive'",
                },
            ),
        ]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        self.provider.row_transformer.transform_rows.assert_awaited_once()
        operations = self.provider.row_transformer.transform_rows.await_args.args[2]
        assert len(operations) == 2
        assert operations[0].operation == "insert"
        assert operations[1].operation == "delete"

    @pytest.mark.asyncio
    async def test_transform_cloud_file_keeps_row_operation_order_across_non_row_rules(self):
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name", "status"]
        mock_df.filter.return_value = mock_df
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "A", "status": "active"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        self.provider.row_transformer = MagicMock()
        row_result = MagicMock()
        row_result.success = True
        row_result.affected_rows = 1
        self.provider.row_transformer.transform_rows = AsyncMock(return_value=row_result)

        rules = [
            TransformRule(
                rule_name="Insert row",
                type="insert_row",
                params={
                    "identifier_type": "index",
                    "index": 0,
                    "data": {"id": 999, "name": "X", "status": "active"},
                    "position": "after",
                },
            ),
            TransformRule(
                rule_name="Keep active",
                type="filter",
                params={"expression": "pl.col('status') == 'active'"},
            ),
            TransformRule(
                rule_name="Delete by values",
                type="delete_row",
                params={
                    "identifier_type": "values",
                    "match_values": {"id": 123},
                },
            ),
        ]

        result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        assert self.provider.row_transformer.transform_rows.await_count == 2

    @pytest.mark.asyncio
    async def test_transform_cloud_file_uses_lazy_fast_path_when_enabled(self):
        self.storage_mock.download_to_temp_file.return_value = "input.csv"
        self.storage_mock.upload_file.return_value = MagicMock(
            download_url="http://uploaded.com/result.csv",
            file_id="file-123",
            version_id="ver-1",
        )

        rules = [
            TransformRule(
                rule_name="Keep all",
                type="filter",
                params={"expression": "pl.lit(True)"},
            )
        ]

        mock_lazy = MagicMock()
        mock_lazy.collect_schema.return_value.names.return_value = ["id", "name"]
        mock_lazy.filter.return_value = mock_lazy
        mock_lazy.sink_csv.return_value = None

        with patch("app.layer4_frameworks.providers.transformation.polars_transformer_provider.settings.TRANSFORM_LAZY_CSV_FAST_PATH_ENABLED", True), \
             patch("app.layer4_frameworks.providers.transformation.polars_transformer_provider.os.path.exists", return_value=False), \
             patch("app.layer4_frameworks.providers.transformation.polars_transformer_provider.pl.scan_csv", return_value=mock_lazy), \
             patch("app.layer4_frameworks.providers.transformation.polars_transformer_provider.pl.read_csv") as mock_read_csv:
            preview_df = MagicMock()
            preview_df.height = 10
            preview_df.to_dicts.return_value = [{"id": "1", "name": "a"}]
            mock_read_csv.return_value = preview_df
            result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        self.storage_mock.download_and_read.assert_not_called()
        self.storage_mock.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_cloud_file_falls_back_when_lazy_fast_path_fails(self):
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_df.columns = ["id", "name"]
        mock_df.filter.return_value = mock_df
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test"}]
        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"

        self.storage_mock.download_to_temp_file.side_effect = Exception("lazy unavailable")

        rules = [
            TransformRule(
                rule_name="Keep all",
                type="filter",
                params={"expression": "pl.lit(True)"},
            )
        ]

        with patch("app.layer4_frameworks.providers.transformation.polars_transformer_provider.settings.TRANSFORM_LAZY_CSV_FAST_PATH_ENABLED", True):
            result = await self.provider.transform_cloud_file("test.csv", "csv", "csv", rules)

        assert result.success is True
        self.storage_mock.download_and_read.assert_called_once_with(
            "test.csv",
            "csv",
            version_id=None,
        )


@pytest.mark.unit
class TestPolarsSchemaTransformerProvider:
    def setup_method(self):
        self.logger_mock = MagicMock()
        self.storage_mock = MagicMock()
        self.provider = PolarsSchemaTransformerProvider(self.logger_mock, self.storage_mock)

    def test_inspect_schema_reads_storage_and_builds_schema(self):
        mock_df = MagicMock()
        mock_df.columns = ["id", "name"]
        mock_df.dtypes = ["Int64", "String"]
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "Alice"}]

        self.storage_mock.download_and_read.return_value = mock_df

        result = self.provider.inspect_schema(
            "D:/sample.xlsx",
            "xlsx",
            3,
            sheet_names=["Customers", "Orders"],
            merge_sheets=True,
            add_sheet_name_column=True,
        )

        assert isinstance(result, FileSchema)
        assert result.file_format == "xlsx"
        assert result.total_columns == 2
        assert [column.name for column in result.columns] == ["id", "name"]
        self.storage_mock.download_and_read.assert_called_once_with(
            "D:/sample.xlsx",
            "xlsx",
            version_id=None,
            sheet_names=["Customers", "Orders"],
            merge_sheets=True,
            add_sheet_name_column=True,
            header_row=None,
        )

    def test_generate_mapping_returns_mapping_result(self):
        result = self.provider.generate_mapping(
            source_schema={"columns": [{"name": "id", "dtype": "Int64"}]},
            target_schema={
                "columns": [
                    {"name": "customer_id", "description": "id"},
                    {"name": "customer_name", "description": "customer name"},
                ]
            },
            source_type="file",
            mapping_hints=[{"source_field": "id", "target_field": "customer_id"}],
        )

        assert isinstance(result, SchemaMappingResult)
        assert result.mappings[0] == FieldMappingSuggestion(
            source_field="id",
            target_field="customer_id",
            confidence=1.0,
            reason="Matched explicit mapping hint",
            source_dtype="Int64",
            target_description="id",
        )
        assert result.unmapped_source_fields == []
        assert result.unmapped_target_fields == ["customer_name"]
        assert result.suggested_rules == [
            {
                "type": "rename_columns",
                "params": {"mapping": {"id": "customer_id"}},
            },
            {
                "type": "select_columns",
                "params": {"columns": ["customer_id"]},
            },
            {
                "type": "reorder_columns",
                "params": {"columns": ["customer_id"]},
            }
        ]
        assert result.target_schema_columns == ["customer_id", "customer_name"]
        assert "Generated 1 field mappings" in result.message

    def test_generate_mapping_prioritizes_explicit_hints_over_row_id(self):
        result = self.provider.generate_mapping(
            source_schema={
                "columns": [
                    {"name": "__row_id", "dtype": "Int64"},
                    {"name": "id", "dtype": "Int64"},
                ]
            },
            target_schema={"columns": [{"name": "customer_id", "description": "id"}]},
            source_type="file",
            mapping_hints=[{"source_field": "id", "target_field": "customer_id"}],
        )

        assert result.mappings == [
            FieldMappingSuggestion(
                source_field="id",
                target_field="customer_id",
                confidence=1.0,
                reason="Matched explicit mapping hint",
                source_dtype="Int64",
                target_description="id",
            )
        ]
        assert result.unmapped_source_fields == ["__row_id"]

    def test_apply_rules_build_nested_json_creates_structured_output(self):
        df = pl.DataFrame(
            [
                {
                    "BusinessPartner.businessPartner": "BP01",
                    "BusinessPartner.to_BusinessPartnerAddress.0.addressID": "1",
                    "BusinessPartner.to_BusinessPartnerAddress.0.cityName": "HCM",
                    "BusinessPartner.to_BusinessPartnerAddress.1.addressID": "2",
                    "BusinessPartner.to_BusinessPartnerAddress.1.cityName": "DN",
                }
            ]
        )

        transformed = self.provider._apply_rules(
            df,
            [
                {
                    "type": "build_nested_json",
                    "params": {
                        "array_paths": ["BusinessPartner.to_BusinessPartnerAddress"],
                        "root_object": "BusinessPartner",
                        "target_columns": [
                            "BusinessPartner.businessPartner",
                            "BusinessPartner.to_BusinessPartnerAddress.0.addressID",
                            "BusinessPartner.to_BusinessPartnerAddress.0.cityName",
                            "BusinessPartner.to_BusinessPartnerAddress.1.addressID",
                            "BusinessPartner.to_BusinessPartnerAddress.1.cityName",
                        ],
                    },
                }
            ],
        )

        rows = transformed.to_dicts()
        assert len(rows) == 1
        assert rows[0]["BusinessPartner"]["businessPartner"] == "BP01"
        assert rows[0]["BusinessPartner"]["to_BusinessPartnerAddress"][0]["addressID"] == "1"
        assert rows[0]["BusinessPartner"]["to_BusinessPartnerAddress"][1]["cityName"] == "DN"

    def test_apply_rules_combine_columns_join_and_sum(self):
        df = pl.DataFrame(
            [
                {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "qty": 2,
                    "unit_price": 10,
                }
            ]
        )

        transformed = self.provider._apply_rules(
            df,
            [
                {
                    "type": "combine_columns",
                    "params": {
                        "sources": ["first_name", "last_name"],
                        "target": "full_name",
                        "mode": "join",
                        "separator": " ",
                    },
                },
                {
                    "type": "combine_columns",
                    "params": {
                        "sources": ["qty", "unit_price"],
                        "target": "total",
                        "mode": "sum",
                    },
                },
            ],
        )

        rows = transformed.to_dicts()
        assert rows[0]["full_name"] == "Jane Doe"
        assert rows[0]["total"] == 12.0

    def test_apply_rules_split_column_creates_multiple_targets(self):
        df = pl.DataFrame([{"full_name": "Jane Doe"}])

        transformed = self.provider._apply_rules(
            df,
            [
                {
                    "type": "split_column",
                    "params": {
                        "source": "full_name",
                        "targets": ["first_name", "last_name"],
                        "delimiter": " ",
                    },
                }
            ],
        )

        rows = transformed.to_dicts()
        assert rows[0]["first_name"] == "Jane"
        assert rows[0]["last_name"] == "Doe"

    @patch("app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider.pl.DataFrame")
    def test_preview_schema_transform_returns_preview(self, mock_pl_dataframe):
        mock_df = MagicMock()
        mock_transformed = MagicMock()
        mock_transformed.head.return_value = mock_transformed
        mock_transformed.to_dicts.return_value = [{"customer_id": 1}]
        mock_pl_dataframe.return_value = mock_df

        self.provider._apply_rules = MagicMock(return_value=mock_transformed)
        self.provider._build_file_schema = MagicMock(
            return_value=FileSchema(
                columns=[],
                sample_data=[{"customer_id": 1}],
                nested_depth=1,
                file_format="json",
                total_columns=1,
            )
        )
        self.provider._compare_target_schema = MagicMock(
            return_value=(True, [], [])
        )

        result = self.provider.preview_schema_transform(
            sample_data=[{"id": 1}],
            rules=[{"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}}],
            file_format="json",
            target_schema={"columns": [{"name": "customer_id"}]},
        )

        assert result.matches_target_schema is True
        assert result.preview_data == [{"customer_id": 1}]
        mock_pl_dataframe.assert_called_once_with([{"id": 1}])

    @pytest.mark.asyncio
    async def test_execute_schema_transform_uploads_transformed_output(self):
        mock_df = MagicMock()
        mock_transformed = MagicMock()
        mock_transformed.columns = ["customer_id"]
        mock_transformed.__len__ = MagicMock(return_value=2)
        mock_transformed.head.return_value = mock_transformed
        mock_transformed.to_dicts.return_value = [{"customer_id": 1}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "http://uploaded.com/result.csv"
        self.provider._apply_rules = MagicMock(return_value=mock_transformed)
        self.provider._build_file_schema = MagicMock(
            return_value=FileSchema(
                columns=[],
                sample_data=[{"customer_id": 1}],
                nested_depth=1,
                file_format="csv",
                total_columns=1,
            )
        )
        self.provider._compare_target_schema = MagicMock(
            return_value=(True, [], [])
        )

        result = await self.provider.execute_schema_transform(
            "D:/sample.xlsx",
            "xlsx",
            "csv",
            [{"type": "drop_columns", "params": {"columns": ["legacy_id"]}}],
            {"columns": [{"name": "customer_id"}]},
            [{"source_field": "id", "target_field": "customer_id"}],
            None,
            None,
            None,
            ["Customers", "Orders"],
            True,
            True,
        )

        assert result.success is True
        assert result.download_url == "http://uploaded.com/result.csv"
        assert result.applied_rules == [
            {"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}},
            {"type": "select_columns", "params": {"columns": ["customer_id"]}},
            {"type": "reorder_columns", "params": {"columns": ["customer_id"]}},
            {"type": "drop_columns", "params": {"columns": ["legacy_id"]}},
        ]
        self.storage_mock.download_and_read.assert_called_once_with(
            "D:/sample.xlsx",
            "xlsx",
            version_id=None,
            sheet_names=["Customers", "Orders"],
            merge_sheets=True,
            add_sheet_name_column=True,
            header_row=None,
        )
        self.provider._apply_rules.assert_called_once_with(
            mock_df,
            [
                {"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}},
                {"type": "select_columns", "params": {"columns": ["customer_id"]}},
                {"type": "reorder_columns", "params": {"columns": ["customer_id"]}},
                {"type": "drop_columns", "params": {"columns": ["legacy_id"]}},
            ],
        )
        self.storage_mock.upload_dataframe.assert_called_once_with(
            mock_transformed,
            "csv",
            file_id=None,
            version_id=None,
            filename="edited_transformation_datafactory.csv",
        )

    def test_combine_rules_prefers_current_field_mapping_over_stale_mapping_rules(self):
        combined = self.provider._combine_rules(
            [{"source_field": "id", "target_field": "customer_code"}],
            [
                {"type": "rename_columns", "params": {"mapping": {"id": "customer_id"}}},
                {"type": "select_columns", "params": {"columns": ["customer_id"]}},
                {"type": "reorder_columns", "params": {"columns": ["customer_id"]}},
                {"type": "cast", "params": {"column": "customer_code", "dtype": "Utf8"}},
            ],
        )

        assert combined == [
            {"type": "rename_columns", "params": {"mapping": {"id": "customer_code"}}},
            {"type": "select_columns", "params": {"columns": ["customer_code"]}},
            {"type": "reorder_columns", "params": {"columns": ["customer_code"]}},
            {"type": "cast", "params": {"column": "customer_code", "dtype": "Utf8"}},
        ]

    def test_compare_target_schema_returns_missing_and_unexpected_columns(self):
        schema = FileSchema(
            columns=[],
            sample_data=[],
            nested_depth=1,
            file_format="csv",
            total_columns=0,
        )
        schema.columns = [
            MagicMock(name="customer_id", dtype="Int64"),
            MagicMock(name="full_name", dtype="String"),
        ]
        schema.columns[0].name = "customer_id"
        schema.columns[1].name = "full_name"

        matches, missing, unexpected = self.provider._compare_target_schema(
            schema,
            {"columns": [{"name": "customer_id"}, {"name": "email"}]},
        )

        assert matches is False
        assert missing == ["email"]
        assert unexpected == ["full_name"]


@pytest.mark.unit
class TestPolarsRowTransformerProvider:
    """Tests for PolarsRowTransformerProvider"""

    def setup_method(self):
        self.logger_mock = MagicMock()
        self.storage_mock = MagicMock()
        self.provider = PolarsRowTransformerProvider(self.logger_mock, self.storage_mock)

    @pytest.mark.asyncio
    async def test_transform_rows_normalizes_file_format_without_dot(self):
        """Test that file format without leading dot remains unchanged"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=5)
        mock_df.columns = ["id", "name"]
        mock_df.shape = (5, 2)
        mock_df.dtypes = ["Int64", "String"]
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "result.csv"
        self.storage_mock.generate_presigned_url.return_value = "http://download.url/result.csv"

        operations = []

        result = await self.provider.transform_rows("test.csv", "csv", operations)

        assert result.success is True
        self.storage_mock.download_and_read.assert_called_once_with(
            "test.csv",
            "csv",
            version_id=None,
        )

    @pytest.mark.asyncio
    async def test_transform_rows_normalizes_file_format_with_leading_dot(self):
        """Test that file format with leading dot gets normalized (dot removed)"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=5)
        mock_df.columns = ["id", "name"]
        mock_df.shape = (5, 2)
        mock_df.dtypes = ["Int64", "String"]
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "result.csv"
        self.storage_mock.generate_presigned_url.return_value = "http://download.url/result.csv"

        operations = []

        result = await self.provider.transform_rows("test.csv", ".csv", operations)

        assert result.success is True
        # Verify that download_and_read was called with normalized format (without dot)
        self.storage_mock.download_and_read.assert_called_once_with(
            "test.csv",
            "csv",
            version_id=None,
        )

    @pytest.mark.asyncio
    async def test_transform_rows_normalizes_file_format_with_multiple_leading_dots(self):
        """Test that file format with multiple leading dots gets all dots stripped"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=5)
        mock_df.columns = ["id", "name"]
        mock_df.shape = (5, 2)
        mock_df.dtypes = ["Int64", "String"]
        mock_df.head.return_value = mock_df
        mock_df.to_dicts.return_value = [{"id": 1, "name": "test"}]

        self.storage_mock.download_and_read.return_value = mock_df
        self.storage_mock.upload_dataframe.return_value = "result.csv"
        self.storage_mock.generate_presigned_url.return_value = "http://download.url/result.csv"

        operations = []

        result = await self.provider.transform_rows("test.csv", "..csv", operations)

        assert result.success is True
        # Verify that download_and_read was called with normalized format (all leading dots removed)
        self.storage_mock.download_and_read.assert_called_once_with(
            "test.csv",
            "csv",
            version_id=None,
        )

    @pytest.mark.asyncio
    async def test_transform_rows_normalizes_various_file_formats(self):
        """Test file format normalization works for various formats"""
        test_cases = [
            (".json", "json"),
            (".parquet", "parquet"),
            (".xlsx", "xlsx"),
            ("json", "json"),
            ("parquet", "parquet"),
        ]

        for input_format, expected_format in test_cases:
            # Reset mock for each test case
            self.storage_mock.reset_mock()

            mock_df = MagicMock()
            mock_df.__len__ = MagicMock(return_value=5)
            mock_df.columns = ["id", "name"]
            mock_df.shape = (5, 2)
            mock_df.dtypes = ["Int64", "String"]
            mock_df.head.return_value = mock_df
            mock_df.to_dicts.return_value = [{"id": 1, "name": "test"}]

            self.storage_mock.download_and_read.return_value = mock_df
            self.storage_mock.upload_dataframe.return_value = f"result.{expected_format}"
            self.storage_mock.generate_presigned_url.return_value = f"http://download.url/result.{expected_format}"

            operations = []

            result = await self.provider.transform_rows(f"test.{expected_format}", input_format, operations)

            assert result.success is True, f"Failed for format: {input_format}"
            self.storage_mock.download_and_read.assert_called_once_with(
                f"test.{expected_format}", expected_format, version_id=None
            )

    def test_delete_rows_values_matches_numeric_with_string_input(self):
        df = pl.DataFrame(
            {
                "__row_id": [1, 2],
                "name": ["alpha", "beta"],
            }
        )
        identifier = RowIdentifier(type="values", match_values={"__row_id": "1"})

        updated_df, deleted_count = self.provider._delete_rows(df, identifier)

        assert deleted_count == 1
        assert len(updated_df) == 1
        assert updated_df["__row_id"].to_list() == [2]

    def test_get_target_index_values_matches_numeric_with_string_input(self):
        df = pl.DataFrame(
            {
                "__row_id": [1, 2],
                "name": ["alpha", "beta"],
            }
        )
        identifier = RowIdentifier(type="values", match_values={"__row_id": "2"})

        index = self.provider._get_target_index(df, identifier)

        assert index == 1

    def test_update_row_partial_data(self):
        df = pl.DataFrame(
            {
                "id": ["1", "2"],
                "name": ["alpha", "beta"],
                "status": ["old", "old"],
            }
        )
        operation = RowOperation(
            operation="update",
            row_identifier=RowIdentifier(type="values", match_values={"id": "2"}),
            data={"status": "new"},
            use_column_indices=False,
        )

        updated_df, affected = self.provider._process_operation(df, operation)

        assert affected == 1
        assert updated_df["status"].to_list() == ["old", "new"]
        assert updated_df["name"].to_list() == ["alpha", "beta"]

    def test_update_row_with_column_indices(self):
        df = pl.DataFrame(
            {
                "id": ["1", "2"],
                "name": ["alpha", "beta"],
            }
        )
        operation = RowOperation(
            operation="update",
            row_identifier=RowIdentifier(type="index", index=0),
            data={"1": "updated-name"},
            use_column_indices=True,
        )

        updated_df, affected = self.provider._process_operation(df, operation)

        assert affected == 1
        assert updated_df["name"].to_list() == ["updated-name", "beta"]

    def test_update_row_missing_column_raises_error(self):
        df = pl.DataFrame(
            {
                "id": ["1"],
                "name": ["alpha"],
            }
        )
        operation = RowOperation(
            operation="update",
            row_identifier=RowIdentifier(type="index", index=0),
            data={"unknown": "x"},
            use_column_indices=False,
        )

        with pytest.raises(ValueError, match="Column 'unknown' not found"):
            self.provider._process_operation(df, operation)

    def test_update_row_multiple_match_requires_allow_multiple(self):
        df = pl.DataFrame(
            {
                "country": ["VN", "VN", "US"],
                "status": ["old", "old", "old"],
            }
        )
        operation = RowOperation(
            operation="update",
            row_identifier=RowIdentifier(type="condition", expression="pl.col('country') == 'VN'"),
            data={"status": "new"},
            allow_multiple=False,
        )

        with pytest.raises(ValueError, match="Multiple rows match the condition"):
            self.provider._process_operation(df, operation)

    def test_update_row_bulk_with_allow_multiple(self):
        df = pl.DataFrame(
            {
                "country": ["VN", "VN", "US"],
                "status": ["old", "old", "old"],
            }
        )
        operation = RowOperation(
            operation="update",
            row_identifier=RowIdentifier(type="condition", expression="pl.col('country') == 'VN'"),
            data={"status": "new"},
            allow_multiple=True,
        )

        updated_df, affected = self.provider._process_operation(df, operation)

        assert affected == 2
        assert updated_df["status"].to_list() == ["new", "new", "old"]

    @pytest.mark.asyncio
    async def test_transform_rows_limits_preview_columns_by_setting(self):
        df = pl.DataFrame(
            {
                "c1": ["v1"],
                "c2": ["v2"],
                "c3": ["v3"],
                "c4": ["v4"],
            }
        )
        self.storage_mock.download_and_read.return_value = df
        self.storage_mock.upload_dataframe.return_value = MagicMock(download_url="http://download.url/result.csv")

        with patch(
            "app.layer4_frameworks.providers.transformation.polars_row_transformer_provider.settings.ROW_TRANSFORM_PREVIEW_MAX_COLUMNS",
            2,
        ), patch(
            "app.layer4_frameworks.providers.transformation.polars_row_transformer_provider.settings.ROW_TRANSFORM_PREVIEW_MAX_ROWS",
            10,
        ):
            result = await self.provider.transform_rows("test.csv", "csv", [])

        assert result.success is True
        assert len(result.preview_data) == 1
        assert set(result.preview_data[0].keys()) == {"c1", "c2"}
