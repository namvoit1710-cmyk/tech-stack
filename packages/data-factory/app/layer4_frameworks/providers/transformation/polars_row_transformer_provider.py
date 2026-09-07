import polars as pl
import time
from typing import List, Dict, Any, Optional, Union
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.storage_interface import IFileStorageProvider
# Row transformation data structures are domain concepts; imported here (and
# re-exported) so this provider stays a pure implementation of the inner contract.
from app.layer1_domain.entities.transformation import (
    RowIdentifier,
    RowOperation,
    RowTransformResult,
)
from app.layer4_frameworks.monitoring.performance_decorator import track_performance, record_performance_stage
from app.layer4_frameworks.config.app_config import settings

from app.layer4_frameworks.providers.expressions.safe_expression import (
    RuleExpressionError,
    evaluate_expression,
)


class PolarsRowTransformerProvider:
    """
    Handles row-level transformations (insert/delete/update) with flexible row identification.
    Supports three identification methods: index, condition, and exact value matching.
    """

    def __init__(self, logger: ILogger, storage: IFileStorageProvider):
        self.logger = logger
        self.storage = storage

    @track_performance
    async def transform_rows(
        self,
        file_path: str,
        file_format: str,
        operations: List[RowOperation],
        use_column_indices: bool = False,
        version_id: Optional[str] = None,
    ) -> RowTransformResult:
        """
        Perform row-level transformations on a cloud file.

        Args:
            file_path: Path to the file in cloud storage
            file_format: File format (csv, json, parquet, etc.)
            operations: List of row operations to perform
            use_column_indices: If True, treat data keys as column indices instead of header names

        Returns:
            RowTransformResult with operation results
        """
        started_at = time.perf_counter()
        stage = "download"
        stage_started_at = started_at
        try:
            self.logger.info(f"Starting Row Transformations for File: {file_path}")

            # Normalize file format by removing leading dot
            file_format = file_format.lstrip('.')

            # Download and read the file
            df = self.storage.download_and_read(file_path, file_format, version_id=version_id)
            record_performance_stage("download", time.perf_counter() - stage_started_at)
            original_row_count = len(df)
            
            self.logger.info(f"Downloaded DataFrame shape: {df.shape}")
            self.logger.debug(f"Downloaded DataFrame columns: {df.columns}")
            self.logger.debug(f"Downloaded DataFrame dtypes: {df.dtypes}")
            self.logger.debug(f"First few rows of downloaded DataFrame: {df.head(3).to_dicts()}")
            total_affected_rows = 0

            stage = "transform"
            stage_started_at = time.perf_counter()
            # Process each operation
            for operation in operations:
                df, affected_count = self._process_operation(df, operation)
                total_affected_rows += affected_count
                self.logger.debug(f"Operation {operation.operation} affected {affected_count} rows")
                
            # Automatically assign __row_id to any newly inserted rows that don't have one
            if "__row_id" in df.columns:
                null_count = df["__row_id"].null_count()
                if null_count > 0:
                    try:
                        max_id = df.select(
                            pl.col("__row_id").cast(pl.Int64, strict=False).max()
                        ).item()
                        max_id = int(max_id) if max_id is not None else 0
                    except Exception:
                        max_id = 0

                    df = df.with_columns(
                        pl.coalesce(
                            pl.col("__row_id"),
                            (pl.col("__row_id").is_null().cum_sum() + max_id).cast(pl.Utf8)
                        ).alias("__row_id")
                    )

            record_performance_stage("transform", time.perf_counter() - stage_started_at)

            stage = "upload"
            stage_started_at = time.perf_counter()
            stored_file = self.storage.upload_dataframe(
                df,
                file_format,
                file_id=file_path,
                version_id=version_id,
                filename=f"edited_transformation_datafactory.{file_format.lstrip('.')}",
            )
            record_performance_stage("upload", time.perf_counter() - stage_started_at)
            download_url = getattr(stored_file, "download_url", None)
            if not download_url:
                download_url = self.storage.generate_presigned_url(stored_file, "download")

            # Generate a bounded preview to avoid returning huge payloads for wide tables.
            preview_data = self._build_preview_data(df)

            self.logger.info(f"Row transformations complete. Total affected rows: {total_affected_rows}")

            return RowTransformResult(
                success=True,
                affected_rows=total_affected_rows,
                total_rows=len(df),
                message=f"Row transformations completed successfully. Original rows: {original_row_count}, Final rows: {len(df)}",
                preview_data=preview_data,
                download_url=download_url
            )

        except Exception as e:
            elapsed = time.perf_counter() - started_at
            if stage_started_at is not None:
                record_performance_stage(stage, time.perf_counter() - stage_started_at)
            self.logger.error(
                "Row transformation error "
                f"(stage={stage}, elapsed={elapsed:.2f}s): {e}"
            )
            return RowTransformResult(
                success=False,
                affected_rows=0,
                total_rows=0,
                message=(
                    "Row transformation failed "
                    f"(stage={stage}, elapsed={elapsed:.2f}s): {str(e)}"
                )
            )

    def _process_operation(
        self,
        df: pl.DataFrame,
        operation: RowOperation
    ) -> tuple[pl.DataFrame, int]:
        """
        Process a single row operation.

        Returns:
            Tuple of (modified_dataframe, number_of_rows_affected)
        """
        if operation.operation == "delete":
            modified_df, affected_count = self._delete_rows(df, operation.row_identifier)
            return modified_df, affected_count
        elif operation.operation == "insert":
            modified_df, affected_count = self._insert_row(df, operation, operation.use_column_indices)
            return modified_df, affected_count
        elif operation.operation == "update":
            modified_df, affected_count = self._update_row(df, operation, operation.use_column_indices)
            return modified_df, affected_count
        else:
            raise ValueError(f"Unsupported operation: {operation.operation}")

    def _update_row(
        self,
        df: pl.DataFrame,
        operation: RowOperation,
        use_column_indices: bool,
    ) -> tuple[pl.DataFrame, int]:
        """Update one row selected by row_identifier using the provided partial data."""
        if not operation.data:
            raise ValueError("Data must be provided for update operations")

        row_data = self._map_row_data(df, operation.data, use_column_indices)
        target_indices = self._get_target_indices(
            df,
            operation.row_identifier,
            allow_multiple=operation.allow_multiple,
        )
        target_condition = pl.arange(0, pl.len()).is_in(target_indices)

        update_exprs = []
        for col_name, value in row_data.items():
            update_value = str(value) if value is not None else None
            update_exprs.append(
                pl.when(target_condition)
                .then(pl.lit(update_value))
                .otherwise(pl.col(col_name))
                .alias(col_name)
            )

        updated_df = df.with_columns(update_exprs)
        return updated_df, len(target_indices)

    def _map_row_data(
        self,
        df: pl.DataFrame,
        data: Dict[str, Any],
        use_column_indices: bool,
    ) -> Dict[str, Any]:
        if use_column_indices:
            row_data = {}
            for idx_str, value in data.items():
                try:
                    idx = int(idx_str)
                    if 0 <= idx < len(df.columns):
                        col_name = df.columns[idx]
                        row_data[col_name] = value
                    else:
                        raise ValueError(f"Column index {idx} is out of bounds")
                except ValueError:
                    raise ValueError(f"Invalid column index: {idx_str}")
            return row_data

        row_data = {}
        for col_name, value in data.items():
            if col_name not in df.columns:
                raise ValueError(f"Column '{col_name}' not found in dataframe")
            row_data[col_name] = value
        return row_data

    def _delete_rows(self, df: pl.DataFrame, row_identifier: RowIdentifier) -> tuple[pl.DataFrame, int]:
        """
        Delete rows based on the row identifier.

        Returns:
            Tuple of (modified_dataframe, number_of_rows_deleted)
        """
        original_count = len(df)

        if row_identifier.type == "index":
            if row_identifier.index is None:
                raise ValueError("Index must be provided for index-based row identification")

            # Delete row at specific index
            if 0 <= row_identifier.index < len(df):
                df = df.filter(pl.arange(0, pl.len()) != row_identifier.index)
            else:
                raise ValueError(f"Index {row_identifier.index} is out of bounds for dataframe with {len(df)} rows")

        elif row_identifier.type == "condition":
            if not row_identifier.expression:
                raise ValueError("Expression must be provided for condition-based row identification")

            # Evaluate the condition and filter out matching rows
            condition = evaluate_expression(
                row_identifier.expression, columns=df.columns, frame=df
            )
            df = df.filter(~condition)

        elif row_identifier.type == "values":
            if not row_identifier.match_values:
                raise ValueError("Match values must be provided for values-based row identification")

            combined_condition = self._build_values_match_condition(df, row_identifier.match_values)
            df = df.filter(~combined_condition)

        else:
            raise ValueError(f"Unsupported row identifier type: {row_identifier.type}")

        return df, original_count - len(df)

    def _insert_row(
        self,
        df: pl.DataFrame,
        operation: RowOperation,
        use_column_indices: bool
    ) -> tuple[pl.DataFrame, int]:
        """
        Insert a row based on the operation details.

        Returns:
            Tuple of (modified_dataframe, number_of_rows_inserted)
        """
        if not operation.data:
            raise ValueError("Data must be provided for insert operations")

        # Convert data dict to appropriate format
        row_data = self._map_row_data(df, operation.data, use_column_indices)

        # Ensure row_data has all columns from the DataFrame schema
        # For CSV files, keep everything as strings - let Polars handle type conversion
        complete_row_data = {}
        for col in df.columns:
            value = row_data.get(col, None)
            if value is not None:
                # Convert to string since CSV is text-based
                complete_row_data[col] = str(value)
            else:
                complete_row_data[col] = None

        self.logger.debug(f"Original DataFrame schema: {df.schema}")
        self.logger.debug(f"Complete row data: {complete_row_data}")

        # For CSV processing, keep everything as strings to avoid type conflicts
        # Create new row dataframe with all columns as strings
        try:
            new_row_df = pl.DataFrame([complete_row_data], infer_schema_length=1000)

            # Ensure all columns are strings to match CSV nature
            string_columns = {}
            for col in df.columns:
                if col in new_row_df.columns:
                    string_columns[col] = pl.col(col).cast(pl.String)
                else:
                    # Add missing columns as null strings
                    string_columns[col] = pl.lit(None).cast(pl.String)

            new_row_df = new_row_df.with_columns(**string_columns)

            self.logger.debug(f"New row DataFrame schema (all strings): {new_row_df.schema}")

        except Exception as e:
            self.logger.error(f"Failed to create row DataFrame: {e}")
            raise ValueError(f"Unable to create row DataFrame with provided data: {complete_row_data}")

        # For CSV processing, convert everything to strings to avoid type conflicts
        # Temporarily convert original DataFrame to all strings for concatenation
        original_schema = df.schema

        # Convert original DataFrame to all strings
        string_columns = {}
        for col in df.columns:
            string_columns[col] = pl.col(col).cast(pl.String)
        df_strings = df.with_columns(**string_columns)

        # Insert based on position and identifier
        if operation.position == "before":
            target_index = self._get_target_index(df_strings, operation.row_identifier)
            if target_index == 0:
                df_strings = pl.concat([new_row_df, df_strings])
            else:
                df_top = df_strings.head(target_index)
                df_bottom = df_strings.tail(len(df_strings) - target_index)
                df_strings = pl.concat([df_top, new_row_df, df_bottom])

        elif operation.position == "after":
            target_index = self._get_target_index(df_strings, operation.row_identifier)
            if target_index == len(df_strings) - 1:
                df_strings = pl.concat([df_strings, new_row_df])
            else:
                df_top = df_strings.head(target_index + 1)
                df_bottom = df_strings.tail(len(df_strings) - target_index - 1)
                df_strings = pl.concat([df_top, new_row_df, df_bottom])

        elif operation.position == "at_index":
            if operation.row_identifier.type != "index" or operation.row_identifier.index is None:
                raise ValueError("Index must be provided for 'at_index' position")
            target_index = operation.row_identifier.index

            if target_index == 0:
                df_strings = pl.concat([new_row_df, df_strings])
            elif target_index >= len(df_strings):
                df_strings = pl.concat([df_strings, new_row_df])
            else:
                df_top = df_strings.head(target_index)
                df_bottom = df_strings.tail(len(df_strings) - target_index)
                df_strings = pl.concat([df_top, new_row_df, df_bottom])

        else:
            raise ValueError(f"Unsupported position: {operation.position}")

        # For CSV processing, keep everything as strings since CSV is text-based
        # Don't convert back to original types - return as strings
        df = df_strings

        return df, 1  # One row inserted

    def _get_target_index(self, df: pl.DataFrame, row_identifier: RowIdentifier) -> int:
        """
        Get the target index for insert operations based on row identifier.

        Returns:
            The index where the operation should be performed
        """
        target_indices = self._get_target_indices(df, row_identifier, allow_multiple=False)
        return int(target_indices[0])

    def _get_target_indices(
        self,
        df: pl.DataFrame,
        row_identifier: RowIdentifier,
        allow_multiple: bool = False,
    ) -> List[int]:
        if row_identifier.type == "index":
            if row_identifier.index is None:
                raise ValueError("Index must be provided for index-based operations")
            if not (0 <= row_identifier.index < len(df)):
                raise ValueError(f"Index {row_identifier.index} is out of bounds")
            return [int(row_identifier.index)]

        if row_identifier.type == "condition":
            if not row_identifier.expression:
                raise ValueError("Expression must be provided for condition-based operations")

            condition = evaluate_expression(
                row_identifier.expression, columns=df.columns, frame=df
            )
            indexed_df = df.with_row_index("__row_idx")
            matched_indices = indexed_df.filter(condition).select("__row_idx").to_series().to_list()

            if len(matched_indices) == 0:
                raise ValueError("No rows match the specified condition")
            if not allow_multiple and len(matched_indices) > 1:
                raise ValueError("Multiple rows match the condition. Please use a more specific condition.")

            return [int(i) for i in matched_indices]

        if row_identifier.type == "values":
            if not row_identifier.match_values:
                raise ValueError("Match values must be provided for values-based operations")

            combined_condition = self._build_values_match_condition(df, row_identifier.match_values)
            indexed_df = df.with_row_index("__row_idx")
            matched_indices = indexed_df.filter(combined_condition).select("__row_idx").to_series().to_list()

            if len(matched_indices) == 0:
                raise ValueError("No rows match the specified values")
            if not allow_multiple and len(matched_indices) > 1:
                raise ValueError("Multiple rows match the values. Please use more specific criteria.")

            return [int(i) for i in matched_indices]

        raise ValueError(f"Unsupported row identifier type: {row_identifier.type}")

    def _build_values_match_condition(
        self,
        df: pl.DataFrame,
        match_values: Dict[str, Any],
    ) -> pl.Expr:
        conditions = []

        for col_name, value in match_values.items():
            if col_name not in df.columns:
                raise ValueError(f"Column '{col_name}' not found in dataframe")

            if value is None:
                conditions.append(pl.col(col_name).is_null())
            else:
                conditions.append(
                    pl.col(col_name).cast(pl.Utf8, strict=False) == str(value)
                )

        combined_condition = conditions[0]
        for condition in conditions[1:]:
            combined_condition = combined_condition & condition

        return combined_condition

    def _build_preview_data(self, df: pl.DataFrame) -> List[Dict[str, Any]]:
        max_rows = max(0, settings.ROW_TRANSFORM_PREVIEW_MAX_ROWS)
        max_cols = max(1, settings.ROW_TRANSFORM_PREVIEW_MAX_COLUMNS)

        if max_rows == 0:
            return []

        preview_df = df.head(max_rows)
        if len(preview_df.columns) > max_cols:
            preview_columns = preview_df.columns[:max_cols]
            self.logger.info(
                "Preview data truncated from "
                f"{len(preview_df.columns)} to {max_cols} columns for response payload"
            )
            preview_df = preview_df.select(preview_columns)

        return preview_df.to_dicts()
