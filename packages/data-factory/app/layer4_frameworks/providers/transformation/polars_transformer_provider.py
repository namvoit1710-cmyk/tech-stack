import polars as pl
import tempfile
import os
from typing import List, Dict, Any
from app.layer2_application.interfaces.transformer_interface import ITransformerProvider
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.storage_interface import (
    IFileStorageProvider,
    FileVersionConflictError,
)
from app.layer1_domain.entities.transformation import TransformRule, TransformResult
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.monitoring.performance_decorator import track_performance
from app.layer4_frameworks.providers.adaptive_batching import resolve_adaptive_batch_size, resolve_runtime_batch_size
from .polars_row_transformer_provider import PolarsRowTransformerProvider, RowOperation, RowIdentifier

from app.layer4_frameworks.providers.expressions.safe_expression import (
    RuleExpressionError,
    evaluate_expression,
)


SYSTEM_ROW_ID_COLUMNS = {"__row_id"}

class PolarsTransformerProvider(ITransformerProvider):
    def __init__(self, logger: ILogger, storage: IFileStorageProvider):
        self.logger = logger
        self.storage = storage
        self.row_transformer = PolarsRowTransformerProvider(logger, storage)

    @track_performance
    async def transform_cloud_file(
        self,
        file_id: str,
        file_format: str,
        output_format: str,
        rules: List[TransformRule],
        version_id: str | None = None,
    ) -> TransformResult:
        try:
            self.logger.info(f"Starting Transformation Engine for File: {file_id}")

            lazy_result = self._try_transform_csv_lazy_fast_path(
                file_id,
                file_format,
                output_format,
                rules,
                version_id,
            )
            if lazy_result is not None:
                return lazy_result
            
            # 1. Download & Read
            df = self.storage.download_and_read(file_id, file_format, version_id=version_id)
            original_rows = len(df)
            
            # 2. Transform Logic
            if isinstance(df, pl.DataFrame) and self._can_use_batched_execution(rules):
                df = self._apply_rules_in_batches(df, rules)
            else:
                df = await self._apply_rules(df, rules, file_id, file_format, version_id)
            
            # 3. Upload Result
            self.logger.info("Transformation complete. Uploading result...")
            stored_file = self.storage.upload_dataframe(
                df,
                output_format,
                file_id=file_id,
                version_id=version_id,
                filename=f"edited_transformation_datafactory.{output_format.lstrip('.')}",
            )
            download_url = getattr(stored_file, "download_url", stored_file)
            output_file_id = getattr(stored_file, "file_id", self._extract_file_id(download_url))
            output_version_id = getattr(stored_file, "version_id", "")
            
            # Return preview data (top 10 rows)
            preview_data = df.head(10).to_dicts()
            
            self.logger.info(f"Upload successful: {download_url}")
            return TransformResult(
                success=True, 
                total_rows=len(df),
                total_columns=len(df.columns),
                odata={"result_file_url": download_url, "preview": preview_data}, 
                message=f"Transformed successfully (Input rows: {original_rows}, Output: {len(df)})",
                download_url=download_url,
                output_file_id=output_file_id,
                output_version_id=output_version_id,
                source_file_id=file_id,
                source_version_id=version_id,
            )
        except FileVersionConflictError as conflict:
            self.logger.warning(f"Transform Error: {conflict}")
            return TransformResult(
                success=False,
                total_rows=0,
                total_columns=0,
                odata=None,
                message=str(conflict),
                source_file_id=file_id,
                source_version_id=version_id,
                current_version_id=conflict.current_version_id,
                stale_version=True,
            )
        except Exception as e:
            self.logger.error(f"Transform Error: {e}")
            return TransformResult(success=False, total_rows=0, total_columns=0, odata=None, message=str(e))

    async def _apply_rules(
        self,
        df: pl.DataFrame,
        rules: List[TransformRule],
        file_id: str,
        file_format: str,
        version_id: str | None,
    ) -> pl.DataFrame:
        index = 0
        while index < len(rules):
            rule = rules[index]
            try:
                self.logger.debug(f"Applying Transformation Rule: {rule.rule_name} (Type: {rule.type})")
                if rule.type in {"filter", "mapping", "drop_columns", "rename_columns", "case_when_expr"}:
                    df = self._apply_dataframe_rule(df, rule)
                elif rule.type in {"insert_row", "delete_row", "update_row"}:
                    group_rules = []
                    while index < len(rules) and rules[index].type in {"insert_row", "delete_row", "update_row"}:
                        group_rules.append(rules[index])
                        index += 1

                    operations: List[RowOperation] = []
                    use_column_indices = False
                    for row_rule in group_rules:
                        operation, operation_use_indices = self._build_row_operation(row_rule)
                        operations.append(operation)
                        use_column_indices = use_column_indices or operation_use_indices

                    result = await self.row_transformer.transform_rows(
                        file_id,
                        file_format,
                        operations,
                        use_column_indices,
                        version_id=version_id,
                    )

                    if not result.success:
                        for row_rule in group_rules:
                            self.logger.warning(f"Row operation failed for rule {row_rule.rule_name}: {result.message}")
                    else:
                        self.logger.debug(
                            f"Applied {len(group_rules)} contiguous row operations affecting {result.affected_rows} row(s)"
                        )
                    continue
            except Exception as eval_err:
                self.logger.warning(f"Failed rule {rule.rule_name}: {eval_err}")
            index += 1
        return df

    def _build_row_operation(self, rule: TransformRule) -> tuple[RowOperation, bool]:
        row_identifier = self._build_row_identifier(rule.params)
        if rule.type == "insert_row":
            use_column_indices = rule.params.get("use_column_indices", False)
            operation = RowOperation(
                operation="insert",
                row_identifier=row_identifier,
                data=rule.params.get("data", {}),
                position=rule.params.get("position", "after"),
                use_column_indices=use_column_indices,
            )
            return operation, use_column_indices

        if rule.type == "delete_row":
            operation = RowOperation(
                operation="delete",
                row_identifier=row_identifier,
            )
            return operation, False

        if rule.type == "update_row":
            use_column_indices = rule.params.get("use_column_indices", False)
            operation = RowOperation(
                operation="update",
                row_identifier=row_identifier,
                data=rule.params.get("data", {}),
                use_column_indices=use_column_indices,
                allow_multiple=rule.params.get("allow_multiple", False),
            )
            return operation, use_column_indices

        raise ValueError(f"Unsupported row rule type: {rule.type}")

    def _try_transform_csv_lazy_fast_path(
        self,
        file_id: str,
        file_format: str,
        output_format: str,
        rules: List[TransformRule],
        version_id: str | None,
    ) -> TransformResult | None:
        if not settings.TRANSFORM_LAZY_CSV_FAST_PATH_ENABLED:
            return None
        if (file_format or "").lstrip(".").lower() != "csv":
            return None
        if (output_format or "").lstrip(".").lower() != "csv":
            return None
        if not self._can_use_batched_execution(rules):
            return None

        download_to_temp = getattr(self.storage, "download_to_temp_file", None)
        upload_file = getattr(self.storage, "upload_file", None)
        if not callable(download_to_temp) or not callable(upload_file):
            return None

        input_temp = None
        output_temp = None
        try:
            input_temp = download_to_temp(file_id, "csv", version_id=version_id)
            lazy_df = pl.scan_csv(input_temp, infer_schema_length=0, ignore_errors=True)
            columns = list(lazy_df.collect_schema().names())

            for rule in rules:
                lazy_df, columns = self._apply_lazy_rule(lazy_df, columns, rule)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tf:
                output_temp = tf.name

            if settings.TRANSFORM_LAZY_CSV_SINK_ENABLED and hasattr(lazy_df, "sink_csv"):
                lazy_df.sink_csv(output_temp)
            else:
                lazy_df.collect().write_csv(output_temp)

            uploaded = upload_file(
                output_temp,
                file_format="csv",
                file_id=file_id,
                version_id=version_id,
                filename="edited_transformation_datafactory.csv",
            )

            output_rows = pl.read_csv(output_temp, infer_schema_length=0, ignore_errors=True).height
            preview_data = pl.read_csv(
                output_temp,
                infer_schema_length=0,
                ignore_errors=True,
                n_rows=10,
            ).to_dicts()

            download_url = getattr(uploaded, "download_url", "")
            output_file_id = getattr(uploaded, "file_id", self._extract_file_id(download_url))
            output_version_id = getattr(uploaded, "version_id", "")

            self.logger.info("Transformation complete via lazy CSV fast path.")
            return TransformResult(
                success=True,
                total_rows=output_rows,
                total_columns=len(columns),
                odata={"result_file_url": download_url, "preview": preview_data},
                message=f"Transformed successfully via lazy CSV fast path (Output: {output_rows})",
                download_url=download_url,
                output_file_id=output_file_id,
                output_version_id=output_version_id,
                source_file_id=file_id,
                source_version_id=version_id,
            )
        except Exception as exc:
            self.logger.warning(f"Lazy CSV fast path failed; falling back to default DataFrame flow: {exc}")
            return None
        finally:
            if output_temp and os.path.exists(output_temp):
                os.remove(output_temp)
            if input_temp and input_temp != file_id and os.path.exists(input_temp):
                os.remove(input_temp)

    def _apply_lazy_rule(
        self,
        lazy_df: pl.LazyFrame,
        columns: List[str],
        rule: TransformRule,
    ) -> tuple[pl.LazyFrame, List[str]]:
        if rule.type == "filter":
            lazy_df = lazy_df.filter(
                evaluate_expression(rule.params.get("expression"), columns=columns)
            )
            return lazy_df, columns

        if rule.type == "mapping":
            new_col = rule.params.get("new_col")
            lazy_df = lazy_df.with_columns(
                evaluate_expression(
                    rule.params.get("expression"), columns=columns
                ).alias(new_col)
            )
            if new_col and new_col not in columns:
                columns = [*columns, new_col]
            return lazy_df, columns

        if rule.type == "drop_columns":
            cols_to_drop = self._expand_row_id_aliases(rule.params.get("columns_to_drop", []))
            valid_drops = [column for column in cols_to_drop if column in columns]
            if valid_drops:
                lazy_df = lazy_df.drop(valid_drops)
                columns = [column for column in columns if column not in set(valid_drops)]
            return lazy_df, columns

        if rule.type == "rename_columns":
            mapping = rule.params.get("mapping", {})
            valid_mapping = {key: value for key, value in mapping.items() if key in columns}
            if valid_mapping:
                lazy_df = lazy_df.rename(valid_mapping)
                columns = [valid_mapping.get(column, column) for column in columns]
            return lazy_df, columns

        if rule.type == "case_when_expr":
            dest_col = rule.params.get("column_dest")
            conditions = rule.params.get("conditions", [])
            otherwise_val = rule.params.get("otherwise")
            if not dest_col or not conditions:
                return lazy_df, columns

            first = conditions[0]
            cond_expr = evaluate_expression(first["when_expression"], columns=columns)
            chain = pl.when(cond_expr).then(pl.lit(first["result"]))
            for condition in conditions[1:]:
                c_expr = evaluate_expression(
                    condition["when_expression"], columns=columns
                )
                chain = chain.when(c_expr).then(pl.lit(condition["result"]))
            if otherwise_val is not None:
                chain = chain.otherwise(pl.lit(otherwise_val))
            else:
                chain = chain.otherwise(None)
            lazy_df = lazy_df.with_columns(chain.alias(dest_col))
            if dest_col not in columns:
                columns = [*columns, dest_col]
            return lazy_df, columns

        return lazy_df, columns

    def _apply_rules_in_batches(self, df: pl.DataFrame, rules: List[TransformRule]) -> pl.DataFrame:
        total_rows = len(df)
        if total_rows == 0:
            return df

        batch_size = resolve_adaptive_batch_size(df, total_rows, self.logger, workload="transform")
        self.logger.info(
            f"Applying transformation rules with dynamic batches (Initial Batch Size: {batch_size})..."
        )

        transformed_batches: List[pl.DataFrame] = []
        cursor = 0
        batch_num = 0
        while cursor < total_rows:
            batch_num += 1
            if (batch_num - 1) % max(1, settings.ADAPTIVE_BATCH_REEVALUATE_EVERY_N_BATCHES) == 0:
                remaining_rows = total_rows - cursor
                batch_size = resolve_runtime_batch_size(
                    df,
                    remaining_rows,
                    batch_size,
                    self.logger,
                    workload="transform",
                )

            end_row = min(cursor + batch_size, total_rows)
            batch = df.slice(cursor, batch_size)
            for rule in rules:
                try:
                    batch = self._apply_dataframe_rule(batch, rule)
                except Exception as eval_err:
                    self.logger.warning(f"Failed rule {rule.rule_name}: {eval_err}")
            transformed_batches.append(batch)
            cursor = end_row

        if not transformed_batches:
            return df.clear()
        if len(transformed_batches) == 1:
            return transformed_batches[0]
        return pl.concat(transformed_batches, how="vertical_relaxed", rechunk=True)

    def _apply_dataframe_rule(self, df: pl.DataFrame, rule: TransformRule) -> pl.DataFrame:
        if rule.type == "filter":
            return df.filter(
                evaluate_expression(
                    rule.params.get("expression"), columns=df.columns, frame=df
                )
            )

        if rule.type == "mapping":
            return df.with_columns(
                evaluate_expression(
                    rule.params.get("expression"), columns=df.columns, frame=df
                ).alias(rule.params.get("new_col"))
            )

        if rule.type == "drop_columns":
            cols_to_drop = self._expand_row_id_aliases(rule.params.get("columns_to_drop", []))
            valid_drops = [column for column in cols_to_drop if column in df.columns]
            if valid_drops:
                self.logger.debug(f"Successfully dropped columns: {valid_drops}")
                return df.drop(valid_drops)
            self.logger.warning(f"Columns to drop not found in dataset: {cols_to_drop}")
            return df

        if rule.type == "rename_columns":
            mapping = rule.params.get("mapping", {})
            valid_mapping = {key: value for key, value in mapping.items() if key in df.columns}
            if valid_mapping:
                self.logger.debug(f"Successfully renamed columns: {valid_mapping}")
                return df.rename(valid_mapping)
            return df

        if rule.type == "case_when_expr":
            dest_col = rule.params.get("column_dest")
            conditions = rule.params.get("conditions", [])
            otherwise_val = rule.params.get("otherwise")
            if not dest_col or not conditions:
                return df

            first = conditions[0]
            cond_expr = evaluate_expression(
                first["when_expression"], columns=df.columns, frame=df
            )
            chain = pl.when(cond_expr).then(pl.lit(first["result"]))
            for condition in conditions[1:]:
                c_expr = evaluate_expression(
                    condition["when_expression"], columns=df.columns, frame=df
                )
                chain = chain.when(c_expr).then(pl.lit(condition["result"]))
            if otherwise_val is not None:
                chain = chain.otherwise(pl.lit(otherwise_val))
            else:
                chain = chain.otherwise(None)
            return df.with_columns(chain.alias(dest_col))

        return df

    def _can_use_batched_execution(self, rules: List[TransformRule]) -> bool:
        allowed_types = {"filter", "mapping", "drop_columns", "rename_columns", "case_when_expr"}
        if not rules or any(rule.type not in allowed_types for rule in rules):
            return False

        return all(self._is_rule_batch_safe(rule) for rule in rules)

    def _is_rule_batch_safe(self, rule: TransformRule) -> bool:
        expressions = []
        if rule.type == "filter":
            expressions.append(str(rule.params.get("expression", "")))
        elif rule.type == "mapping":
            expressions.append(str(rule.params.get("expression", "")))
        elif rule.type == "case_when_expr":
            expressions.extend(str(item.get("when_expression", "")) for item in rule.params.get("conditions", []))

        forbidden_tokens = (
            ".sum(",
            ".mean(",
            ".min(",
            ".max(",
            ".count(",
            ".len(",
            ".over(",
            ".rank(",
            ".shift(",
            ".cum_",
            ".rolling_",
            ".sort_by(",
            "pl.len(",
        )
        return not any(token in expression for expression in expressions for token in forbidden_tokens)

    def _build_row_identifier(self, params: Dict[str, Any]) -> RowIdentifier:
        """
        Build a RowIdentifier from rule parameters.

        Supports three identification methods:
        - index: by row index position
        - condition: by expression evaluation
        - values: by exact value matching
        """
        identifier_type = params.get("identifier_type")
        if not identifier_type:
            raise ValueError("identifier_type must be specified for row operations")

        if identifier_type == "index":
            index = params.get("index")
            if index is None:
                raise ValueError("index must be provided for index-based row identification")
            return RowIdentifier(type="index", index=int(index))

        elif identifier_type == "condition":
            expression = params.get("expression")
            if not expression:
                raise ValueError("expression must be provided for condition-based row identification")
            return RowIdentifier(type="condition", expression=expression)

        elif identifier_type == "values":
            match_values = params.get("match_values")
            if not match_values:
                raise ValueError("match_values must be provided for values-based row identification")
            return RowIdentifier(type="values", match_values=match_values)

        else:
            raise ValueError(f"Unsupported identifier_type: {identifier_type}")

    def _extract_file_id(self, download_url: str) -> str:
        if not download_url:
            return ""
        return str(download_url).rstrip("/").split("/")[-1]

    def _expand_row_id_aliases(self, columns: List[str]) -> List[str]:
        expanded: List[str] = []
        seen = set()
        for column in columns:
            if column not in seen:
                expanded.append(column)
                seen.add(column)
            if column in SYSTEM_ROW_ID_COLUMNS:
                for alias in SYSTEM_ROW_ID_COLUMNS:
                    if alias not in seen:
                        expanded.append(alias)
                        seen.add(alias)
        return expanded
