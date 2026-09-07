from typing import List, Dict, Any, Optional, Tuple
import re

import polars as pl

from app.layer1_domain.entities.schema_transform import (
    SchemaField,
    FileSchema,
    SchemaTransformPreview,
    SchemaTransformExecutionResult,
    FieldMappingSuggestion,
    SchemaMappingResult,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.storage_interface import IFileStorageProvider
from app.layer2_application.interfaces.schema_transformer_interface import (
    ISchemaTransformerProvider,
)
from app.layer4_frameworks.providers.conditions import build_condition
from app.layer4_frameworks.monitoring.performance_decorator import track_performance

from app.layer4_frameworks.providers.expressions.safe_expression import (
    RuleExpressionError,
    evaluate_expression,
)


SYSTEM_ROW_ID_COLUMNS = {"__row_id"}


class PolarsSchemaTransformerProvider(ISchemaTransformerProvider):
    def __init__(self, logger: ILogger, storage: IFileStorageProvider):
        self.logger = logger
        self.storage = storage

    @track_performance
    def inspect_schema(
        self,
        file_path: str,
        file_format: str,
        sample_size: int = 5,
        version_id: Optional[str] = None,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> FileSchema:
        normalized_format = self._normalize_file_format(file_format, file_path)
        df = self.storage.download_and_read(
            file_path,
            normalized_format,
            version_id=version_id,
            sheet_names=sheet_names,
            merge_sheets=merge_sheets,
            add_sheet_name_column=add_sheet_name_column,
            header_row=header_row,
        )
        return self._build_file_schema(
            df,
            normalized_format,
            sample_size,
            selected_sheets=sheet_names or [],
            merged_sheets=merge_sheets,
        )

    @track_performance
    def generate_mapping(
        self,
        source_schema: Dict[str, Any],
        target_schema: Dict[str, Any],
        source_type: str = "file",
        mapping_hints: Optional[List[Dict[str, Any]]] = None,
    ) -> SchemaMappingResult:
        source_fields = self._extract_source_fields(source_schema)
        target_fields = self._extract_target_fields(target_schema)
        source_by_name = {field["name"]: field for field in source_fields}
        target_by_name = {field["name"]: field for field in target_fields}

        mappings: List[FieldMappingSuggestion] = []
        used_targets = set()
        mapping_hints = mapping_hints or []

        for hint in mapping_hints:
            source_name = (
                hint.get("source_field")
                or hint.get("source")
                or hint.get("source_column")
                or hint.get("from")
            )
            target_name = (
                hint.get("target_field")
                or hint.get("target")
                or hint.get("target_column")
                or hint.get("to")
            )
            if not source_name or not target_name:
                continue
            if source_name not in source_by_name or target_name not in target_by_name:
                continue
            if target_name in used_targets:
                continue

            source_field = source_by_name[source_name]
            target_field = target_by_name[target_name]
            mappings.append(
                FieldMappingSuggestion(
                    source_field=source_name,
                    target_field=target_name,
                    confidence=1.0,
                    reason="Matched explicit mapping hint",
                    source_dtype=source_field.get("dtype", ""),
                    target_description=target_field.get("description", ""),
                )
            )
            used_targets.add(target_name)

        mapped_source_fields = {mapping.source_field for mapping in mappings}

        for source_field in source_fields:
            source_name = source_field["name"]
            if source_name in mapped_source_fields:
                continue
            source_dtype = source_field.get("dtype", "")
            best_match = None
            best_score = 0.0
            best_reason = ""

            for target_field in target_fields:
                target_name = target_field["name"]
                if target_name in used_targets:
                    continue

                score, reason = self._score_field_mapping(
                    source_name,
                    target_name,
                    target_field.get("description", ""),
                    mapping_hints,
                )
                if score > best_score:
                    best_score = score
                    best_match = target_field
                    best_reason = reason

            if best_match and best_score >= 0.45:
                target_name = best_match["name"]
                used_targets.add(target_name)
                mappings.append(
                    FieldMappingSuggestion(
                        source_field=source_name,
                        target_field=target_name,
                        confidence=round(best_score, 4),
                        reason=best_reason,
                        source_dtype=source_dtype,
                        target_description=best_match.get("description", ""),
                    )
                )
                mapped_source_fields.add(source_name)

        mapped_target_fields = {mapping.target_field for mapping in mappings}

        unmapped_source_fields = [
            field["name"] for field in source_fields if field["name"] not in mapped_source_fields
        ]
        unmapped_target_fields = [
            field["name"] for field in target_fields if field["name"] not in mapped_target_fields
        ]

        suggested_rules = self._field_mapping_to_rules(
            [
                {"source_field": mapping.source_field, "target_field": mapping.target_field}
                for mapping in mappings
            ]
        )

        return SchemaMappingResult(
            mappings=mappings,
            unmapped_source_fields=unmapped_source_fields,
            unmapped_target_fields=unmapped_target_fields,
            suggested_rules=suggested_rules,
            target_schema_columns=list(target_by_name.keys()),
            message=(
                f"Generated {len(mappings)} field mappings for source type '{source_type}'"
            ),
        )

    @track_performance
    def preview_schema_transform(
        self,
        sample_data: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        file_format: str = "json",
        target_schema: Optional[Dict[str, Any]] = None,
    ) -> SchemaTransformPreview:
        if not sample_data:
            raise ValueError("sample_data is required for preview")

        df = pl.DataFrame(sample_data)
        transformed = self._apply_rules(df, rules)
        output_schema = self._build_file_schema(transformed, file_format, sample_size=10)
        (
            matches_target_schema,
            missing_columns,
            unexpected_columns,
        ) = self._compare_target_schema(output_schema, target_schema)

        return SchemaTransformPreview(
            preview_data=transformed.head(10).to_dicts(),
            output_schema=output_schema,
            matches_target_schema=matches_target_schema,
            missing_columns=missing_columns,
            unexpected_columns=unexpected_columns,
        )

    @track_performance
    async def execute_schema_transform(
        self,
        source_file_id: str,
        file_format: str,
        output_format: str,
        rules: List[Dict[str, Any]],
        target_schema: Optional[Dict[str, Any]] = None,
        field_mapping: Optional[List[Dict[str, Any]]] = None,
        source_version_id: Optional[str] = None,
        target_file_id: Optional[str] = None,
        target_version_id: Optional[str] = None,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
        derive_field_paths: bool = False,
        field_path_overrides: Optional[Dict[str, str]] = None,
    ) -> SchemaTransformExecutionResult:
        normalized_input = self._normalize_file_format(file_format, source_file_id)
        normalized_output = self._normalize_file_format(output_format)

        field_paths: Dict[str, str] = {}
        if derive_field_paths:
            df, field_paths = self.storage.download_and_read_with_paths(
                source_file_id,
                normalized_input,
                version_id=source_version_id,
                sheet_names=sheet_names,
                merge_sheets=merge_sheets,
                add_sheet_name_column=add_sheet_name_column,
                header_row=header_row,
                field_path_overrides=field_path_overrides,
            )
        else:
            df = self.storage.download_and_read(
                source_file_id,
                normalized_input,
                version_id=source_version_id,
                sheet_names=sheet_names,
                merge_sheets=merge_sheets,
                add_sheet_name_column=add_sheet_name_column,
                header_row=header_row,
            )
        combined_rules = self._combine_rules(field_mapping, rules)
        transformed = self._apply_rules(df, combined_rules)
        stored_file = self.storage.upload_dataframe(
            transformed,
            normalized_output,
            file_id=target_file_id,
            version_id=target_version_id,
            filename=f"edited_transformation_datafactory.{normalized_output.lstrip('.')}",
        )
        download_url = getattr(stored_file, "download_url", stored_file)
        output_file_id = getattr(stored_file, "file_id", self._extract_file_id(download_url))
        output_version_id = getattr(stored_file, "version_id", "")
        output_schema = self._build_file_schema(
            transformed,
            normalized_output,
            sample_size=10,
            selected_sheets=sheet_names or [],
            merged_sheets=merge_sheets,
        )

        (
            matches_target_schema,
            missing_columns,
            unexpected_columns,
        ) = self._compare_target_schema(output_schema, target_schema)

        message = (
            "Schema transform executed successfully"
            if matches_target_schema is not False
            else (
                "Schema transform executed with schema mismatch. "
                f"Missing columns: {missing_columns}. Unexpected columns: {unexpected_columns}"
            )
        )

        return SchemaTransformExecutionResult(
            success=True,
            total_rows=len(transformed),
            total_columns=len(transformed.columns),
            output_schema=output_schema,
            preview_data=transformed.head(10).to_dicts(),
            download_url=download_url,
            output_file_id=output_file_id,
            output_version_id=output_version_id,
            source_file_id=source_file_id,
            source_version_id=source_version_id,
            applied_rules=combined_rules,
            field_paths=field_paths,
            message=message,
        )

    def _combine_rules(
        self,
        field_mapping: Optional[List[Dict[str, Any]]],
        rules: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        mapping_rules = self._field_mapping_to_rules(field_mapping or [])
        remaining_rules = list(rules or [])
        if mapping_rules:
            remaining_rules = [
                rule
                for rule in remaining_rules
                if rule.get("type") not in {"rename_columns", "select_columns", "reorder_columns"}
            ]
        return mapping_rules + remaining_rules

    def _field_mapping_to_rules(
        self, field_mapping: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        rename_mapping: Dict[str, str] = {}
        ordered_targets: List[str] = []

        for item in field_mapping:
            source_field = (
                item.get("source_field")
                or item.get("source")
                or item.get("source_column")
                or item.get("from")
            )
            target_field = (
                item.get("target_field")
                or item.get("target")
                or item.get("target_column")
                or item.get("to")
            )
            if not source_field or not target_field:
                continue
            if source_field != target_field:
                rename_mapping[source_field] = target_field
            if target_field not in ordered_targets:
                ordered_targets.append(target_field)

        if not rename_mapping and not ordered_targets:
            return []

        rules: List[Dict[str, Any]] = []
        if rename_mapping:
            rules.append(
                {
                    "type": "rename_columns",
                    "params": {"mapping": rename_mapping},
                }
            )
        if ordered_targets:
            rules.append(
                {
                    "type": "select_columns",
                    "params": {"columns": ordered_targets},
                }
            )
            rules.append(
                {
                    "type": "reorder_columns",
                    "params": {"columns": ordered_targets},
                }
            )

        return rules

    def _apply_rules(self, df: pl.DataFrame, rules: List[Dict[str, Any]]) -> pl.DataFrame:
        current = df

        for index, rule in enumerate(rules or []):
            rule_type = rule.get("type", "").strip()
            params = rule.get("params", {})
            self.logger.debug(
                f"Applying schema transform rule [{index + 1}/{len(rules)}]: {rule_type}"
            )

            if rule_type == "rename_columns":
                mapping = params.get("mapping", {})
                valid_mapping = {k: v for k, v in mapping.items() if k in current.columns}
                if valid_mapping:
                    # Two source columns mapped onto one name yields a bare
                    # "column X is duplicate" from Polars, which names neither the
                    # rule nor the sources. Say which labels collided.
                    collisions: Dict[str, List[str]] = {}
                    for source, target in valid_mapping.items():
                        collisions.setdefault(target, []).append(source)
                    clashing = {
                        target: sources
                        for target, sources in collisions.items()
                        if len(sources) > 1
                    }
                    if clashing:
                        raise ValueError(
                            "rename_columns would map several columns onto the same "
                            f"name: {clashing}. Give each target a distinct name."
                        )
                    current = current.rename(valid_mapping)

            elif rule_type == "drop_columns":
                columns = params.get("columns") or params.get("columns_to_drop") or []
                columns = self._expand_row_id_aliases(columns)
                valid_columns = [c for c in columns if c in current.columns]
                if valid_columns:
                    current = current.drop(valid_columns)

            elif rule_type == "select_columns":
                columns = params.get("columns", [])
                valid_columns = [c for c in columns if c in current.columns]
                if valid_columns:
                    current = current.select(valid_columns)

            elif rule_type == "reorder_columns":
                ordered = params.get("columns", [])
                remaining = [c for c in current.columns if c not in ordered]
                final_order = [c for c in ordered if c in current.columns] + remaining
                current = current.select(final_order)

            elif rule_type == "cast":
                # ``column`` (one) or ``columns`` (many) — a template that types
                # every field would otherwise need one rule per column.
                columns = params.get("columns") or []
                single = params.get("column")
                if single:
                    columns = [single, *columns]
                dtype_name = params.get("dtype", "Utf8")
                date_format = params.get("format")
                strict = bool(params.get("strict", True))
                expressions = [
                    self._build_cast_expr(current, name, dtype_name, date_format, strict)
                    for name in columns
                    if name in current.columns
                ]
                if expressions:
                    current = current.with_columns(expressions)

            elif rule_type == "explode":
                column = params.get("column")
                if column and column in current.columns:
                    current = current.explode(column)

            elif rule_type == "unnest":
                column = params.get("column")
                if column and column in current.columns:
                    try:
                        current = current.unnest(column)
                    except Exception:
                        self.logger.warning(
                            f"Unable to unnest column '{column}'. Skipping rule."
                        )

            elif rule_type == "flatten_struct":
                column = params.get("column")
                prefix = params.get("prefix", "")
                if column and column in current.columns:
                    try:
                        current = current.unnest(column)
                        if prefix:
                            renamed = {
                                c: f"{prefix}{c}"
                                for c in current.columns
                                if c != column and not c.startswith(prefix)
                            }
                            if renamed:
                                current = current.rename(renamed)
                    except Exception:
                        self.logger.warning(
                            f"Unable to flatten struct column '{column}'. Skipping rule."
                        )

            elif rule_type == "mapping":
                expression = params.get("expression")
                new_col = params.get("new_col")
                if expression and new_col:
                    current = current.with_columns(
                        evaluate_expression(
                            expression, columns=current.columns, frame=current
                        ).alias(new_col)
                    )

            elif rule_type == "combine_columns":
                target_col = str(params.get("target") or "").strip()
                source_cols = [
                    str(item).strip() for item in (params.get("sources") or []) if str(item).strip()
                ]
                mode = str(params.get("mode") or "join").strip().lower()
                separator = str(params.get("separator") or " ")
                valid_source_cols = [name for name in source_cols if name in current.columns]
                if target_col and valid_source_cols:
                    if mode == "sum":
                        expression = pl.lit(0.0)
                        for source_name in valid_source_cols:
                            expression = expression + pl.coalesce(
                                [pl.col(source_name).cast(pl.Float64, strict=False), pl.lit(0.0)]
                            )
                        current = current.with_columns(expression.alias(target_col))
                    else:
                        expression_parts = [
                            pl.col(source_name).cast(pl.Utf8, strict=False)
                            for source_name in valid_source_cols
                        ]
                        current = current.with_columns(
                            pl.concat_str(expression_parts, separator=separator, ignore_nulls=True).alias(target_col)
                        )

            elif rule_type == "split_column":
                source_col = str(params.get("source") or "").strip()
                target_cols = [
                    str(item).strip() for item in (params.get("targets") or []) if str(item).strip()
                ]
                delimiter = str(params.get("delimiter") or " ")
                if source_col and source_col in current.columns and target_cols:
                    split_target_count = len(target_cols)
                    split_struct_col = f"__split_{index}"
                    current = current.with_columns(
                        pl.col(source_col)
                        .cast(pl.Utf8, strict=False)
                        .fill_null("")
                        .str.split_exact(delimiter, max(0, split_target_count - 1))
                        .alias(split_struct_col)
                    )
                    current = current.unnest(split_struct_col)
                    rename_map = {
                        f"field_{split_idx}": target_name
                        for split_idx, target_name in enumerate(target_cols)
                        if f"field_{split_idx}" in current.columns
                    }
                    if rename_map:
                        current = current.rename(rename_map)

            elif rule_type == "add_row_index":
                name = str(params.get("name") or "__source_row").strip()
                offset = int(params.get("offset", 1))
                if name not in current.columns:
                    current = current.with_row_index(name=name, offset=offset)

            elif rule_type == "add_technical_fields":
                current = self._add_technical_fields(current, params)

            elif rule_type == "nest_children":
                current = self._nest_children(current, params)

            elif rule_type == "join_reference":
                current = self._join_reference(current, params)

            elif rule_type == "derive":
                current = self._apply_derivation(current, params)

            elif rule_type == "build_nested_json":
                array_paths = [str(item).strip() for item in (params.get("array_paths") or []) if str(item).strip()]
                target_columns = [str(item).strip() for item in (params.get("target_columns") or []) if str(item).strip()]
                root_object = str(params.get("root_object") or "").strip()
                current = self._build_nested_json_frame(current, array_paths, target_columns, root_object)

            else:
                self.logger.warning(f"Unsupported schema transform rule type: {rule_type}")

        return current

    # ------------------------------------------------------------------
    # Typed casting
    # ------------------------------------------------------------------
    def _build_cast_expr(
        self,
        df: pl.DataFrame,
        column: str,
        dtype_name: str,
        date_format: Optional[str],
        strict: bool,
    ) -> pl.Expr:
        """Cast one column, parsing text dates rather than failing on them.

        Polars cannot ``cast`` a Utf8 ``"2025-06-23"`` straight to ``Date`` - it
        needs ``str.to_date``. Business templates carry dates as text, so a plain
        cast would either raise or silently leave the column as text. Non-temporal
        targets keep the original cast behaviour.
        """
        target = self._resolve_polars_dtype(dtype_name)
        source = df.schema.get(column)
        expression = pl.col(column)

        if target in (pl.Date, pl.Datetime) and source == pl.Utf8:
            text = expression.str.strip_chars()
            # An empty cell is "no value", not "unparseable value".
            text = pl.when(text.str.len_chars() == 0).then(None).otherwise(text)
            if target == pl.Date:
                parsed = text.str.to_date(format=date_format, strict=strict)
            else:
                parsed = text.str.to_datetime(format=date_format, strict=strict)
            return parsed.alias(column)

        return expression.cast(target, strict=strict).alias(column)

    # ------------------------------------------------------------------
    # Cross-table rules
    # ------------------------------------------------------------------
    def _join_reference(self, df: pl.DataFrame, params: Dict[str, Any]) -> pl.DataFrame:
        """Left-join columns from another already-materialized table.

        This is what makes a cross-table rule expressible: a derivation whose
        condition lives on the parent table and whose targets live on a child
        table needs the parent field sitting beside the child rows first. Pull it
        in with ``join_reference``, act on it with ``derive``, then drop it.
        """
        file_id = params.get("file_id") or params.get("file_path")
        if not file_id:
            self.logger.warning("join_reference needs file_id/file_path. Skipping rule.")
            return df

        keys = [str(key).strip() for key in (params.get("on") or []) if str(key).strip()]
        if not keys:
            self.logger.warning("join_reference needs 'on' key columns. Skipping rule.")
            return df

        missing_keys = [key for key in keys if key not in df.columns]
        if missing_keys:
            self.logger.warning(
                f"join_reference key column(s) {missing_keys} are not on the frame. Skipping rule."
            )
            return df

        try:
            reference = self.storage.download_and_read(
                file_id,
                self._normalize_file_format(params.get("file_format") or "csv", file_id),
                version_id=params.get("version_id"),
                sheet_names=params.get("sheet_names"),
                header_row=params.get("header_row"),
            )
        except Exception as exc:
            self.logger.warning(
                f"join_reference could not read '{file_id}': {exc}. Skipping rule."
            )
            return df

        missing_reference_keys = [key for key in keys if key not in reference.columns]
        if missing_reference_keys:
            self.logger.warning(
                f"join_reference key column(s) {missing_reference_keys} are not on the "
                "reference. Skipping rule."
            )
            return df

        wanted = [
            str(name).strip()
            for name in (params.get("columns") or [])
            if str(name).strip() and str(name).strip() in reference.columns
        ]
        if not wanted:
            wanted = [name for name in reference.columns if name not in keys]

        reference = reference.select(
            [*keys, *[name for name in wanted if name not in keys]]
        ).unique(subset=keys, keep="first")

        # A key read as i64 on one table and Utf8 on the other must not silently
        # match nothing: align the reference keys to the left frame's dtypes.
        alignments = [
            pl.col(key).cast(df.schema[key], strict=False).alias(key)
            for key in keys
            if reference.schema[key] != df.schema[key]
        ]
        if alignments:
            reference = reference.with_columns(alignments)

        prefix = str(params.get("prefix") or "")
        if prefix:
            reference = reference.rename(
                {
                    name: f"{prefix}{name}"
                    for name in reference.columns
                    if name not in keys
                }
            )

        how = str(params.get("how") or "left").strip().lower()
        return df.join(reference, on=keys, how=how)

    #: The technical columns a deep insert needs, and their default names.
    #: Sourced from the persistence trace: without objectID/reqID/itemID/taskID,
    #: the parent-child keys and MDGDummyKey, a flattened table cannot be put
    #: back together into the multi-level object it came from.
    TECHNICAL_FIELDS = (
        ("object_id", "objectID"),
        ("req_id", "reqID"),
        ("task_id", "taskID"),
        ("status", "MDGStatus"),
        ("modify_type", "MDGModifyType"),
        ("table_name", "tableName"),
        ("parent_table", "parentTable"),
        ("parent_key", "parentKey"),
    )

    def _add_technical_fields(
        self, df: "pl.DataFrame", params: Dict[str, Any]
    ) -> "pl.DataFrame":
        """Stamp the identifiers that survive flattening.

        Constants (objectID, reqID, taskID, MDGStatus, MDGModifyType, and the
        table/parent names) come from the change request and are the same for
        every row of a table. itemID and sourceRow are per row: sourceRow is the
        position in the uploaded file, which is the only thing that can point a
        frontend error back at a cell the user typed.

        A column that already carries a value is left alone — a workbook that
        already has an itemID column should keep it, not be renumbered.
        """
        names = params.get("names") or {}
        current = df

        additions = []
        for key, default_name in self.TECHNICAL_FIELDS:
            value = params.get(key)
            if value is None:
                continue
            column = str(names.get(key) or default_name)
            if column in current.columns:
                self.logger.warning(
                    f"add_technical_fields: '{column}' already exists; leaving it "
                    "as it is rather than overwriting."
                )
                continue
            additions.append(pl.lit(value).alias(column))
        if additions:
            current = current.with_columns(additions)

        source_row = params.get("source_row")
        if source_row:
            column = str(names.get("source_row") or source_row)
            if column not in current.columns:
                # 1-based, and offset past the banner rows so the number matches
                # what the user sees in the spreadsheet.
                offset = int(params.get("source_row_offset", 1))
                current = current.with_row_index(name=column, offset=offset)

        item_id = params.get("item_id")
        if item_id:
            column = str(names.get("item_id") or item_id)
            if column not in current.columns:
                current = current.with_row_index(
                    name=column, offset=int(params.get("item_id_offset", 1))
                )

        return current

    def _nest_children(self, df: pl.DataFrame, params: Dict[str, Any]) -> pl.DataFrame:
        """Collapse child tables into list-of-struct columns on their parent.

        ``build_nested_json`` builds one nested record per input row, so it can only
        produce arrays when the column names already carry the index
        (``to_ArticlePlant.0.plant``). A mass-upload workbook is the other shape:
        children live in their own sheets and repeat the parent key, so five sales-tax
        rows for item 1 must become one five-element array under item 1. That needs a
        group-by, which is what this rule adds.

        Each child is read, grouped on the shared key, aggregated into a struct list,
        and joined back onto the parent. Writing the result as JSON then yields the
        deep object the persistence layer expects.

        Params:
          - on:        key columns shared by parent and children
          - children:  [{file_id|file_path, as, file_format, version_id, header_row,
                        sheet_names, drop_keys, dummy_key}]
                       ``as`` is the nested field name (e.g. ``to_ArticlePlant``).
                       ``dummy_key`` names a column to receive the positional path
                       (``to_ArticlePlant[0]``) that identifies a child record.
        """
        keys = [str(key).strip() for key in (params.get("on") or []) if str(key).strip()]
        if not keys:
            self.logger.warning("nest_children needs 'on' key columns. Skipping rule.")
            return df

        missing_keys = [key for key in keys if key not in df.columns]
        if missing_keys:
            self.logger.warning(
                f"nest_children key column(s) {missing_keys} are not on the parent. Skipping rule."
            )
            return df

        current = df
        for child in params.get("children") or []:
            if not isinstance(child, dict):
                continue
            current = self._nest_one_child(current, keys, child)
        return current

    def _nest_one_child(
        self, parent: pl.DataFrame, keys: List[str], child: Dict[str, Any]
    ) -> pl.DataFrame:
        file_id = child.get("file_id") or child.get("file_path")
        field_name = str(child.get("as") or "").strip()
        if not file_id or not field_name:
            self.logger.warning(
                "nest_children entry needs both file_id/file_path and 'as'. Skipping child."
            )
            return parent

        try:
            frame = self.storage.download_and_read(
                file_id,
                self._normalize_file_format(child.get("file_format") or "csv", file_id),
                version_id=child.get("version_id"),
                sheet_names=child.get("sheet_names"),
                header_row=child.get("header_row"),
            )
        except Exception as exc:
            self.logger.warning(
                f"nest_children could not read child '{field_name}' from '{file_id}': "
                f"{exc}. Skipping child."
            )
            return parent

        missing_keys = [key for key in keys if key not in frame.columns]
        if missing_keys:
            self.logger.warning(
                f"nest_children child '{field_name}' is missing key column(s) "
                f"{missing_keys}. Skipping child."
            )
            return parent

        # Align key dtypes, or a text key on one side silently matches nothing.
        alignments = [
            pl.col(key).cast(parent.schema[key], strict=False).alias(key)
            for key in keys
            if frame.schema[key] != parent.schema[key]
        ]
        if alignments:
            frame = frame.with_columns(alignments)

        dummy_key = str(child.get("dummy_key") or "").strip()
        if dummy_key:
            # Position within the parent's group, so a child record keeps an
            # address that survives flattening: to_ArticlePlant[0].
            frame = frame.with_columns(
                pl.concat_str([
                    pl.lit(f"{field_name}["),
                    pl.int_range(pl.len()).over(keys).cast(pl.Utf8),
                    pl.lit("]"),
                ]).alias(dummy_key)
            )

        drop_keys = bool(child.get("drop_keys", True))
        payload_columns = [
            name for name in frame.columns if not (drop_keys and name in keys)
        ]
        if not payload_columns:
            self.logger.warning(
                f"nest_children child '{field_name}' has no columns left after dropping "
                "the keys. Skipping child."
            )
            return parent

        nested = frame.group_by(keys).agg(
            pl.struct(payload_columns).alias(field_name)
        )
        return parent.join(nested, on=keys, how="left")

    def _apply_derivation(self, df: pl.DataFrame, params: Dict[str, Any]) -> pl.DataFrame:
        """Declarative when/then derivation - the shape a Derivation Rule already has.

        Mirrors the configured rule directly (``when`` condition(s) -> ``then``
        target/value pairs) instead of routing business config through ``eval``.
        """
        condition = self._build_condition_expr(df, params.get("when") or {})
        if condition is None:
            self.logger.warning("derive rule has no usable 'when' condition. Skipping rule.")
            return df

        assignments = [a for a in (params.get("then") or []) if isinstance(a, dict)]
        if not assignments:
            self.logger.warning("derive rule has no 'then' assignments. Skipping rule.")
            return df

        only_when_empty = bool(params.get("only_when_empty", False))
        current = df
        for assignment in assignments:
            column = str(assignment.get("column") or assignment.get("field") or "").strip()
            if not column:
                continue
            value = assignment.get("value")

            if column in current.columns:
                fallback = pl.col(column)
                applies = condition
                if only_when_empty:
                    is_empty = pl.col(column).is_null() | (
                        pl.col(column).cast(pl.Utf8, strict=False).str.strip_chars() == ""
                    )
                    applies = condition & is_empty
            else:
                # Deriving into a column the source file does not carry is
                # legitimate: start it null so untouched rows stay empty.
                fallback = pl.lit(None)
                applies = condition

            current = current.with_columns(
                pl.when(applies).then(pl.lit(value)).otherwise(fallback).alias(column)
            )
        return current

    def _build_condition_expr(
        self, df: pl.DataFrame, when: Dict[str, Any]
    ) -> Optional[pl.Expr]:
        """``when`` is a single condition, or ``{match: ALL|ANY, conditions: [...]}``.

        The grammar itself lives in ``providers.conditions`` because
        ``conditional_required`` speaks it too - one ``when`` shape, one operator
        list, one place to fix. Passing ``df.columns`` preserves this path's
        behaviour: a derivation whose trigger column is absent is skipped with a
        warning, not raised.
        """
        return build_condition(
            when,
            known_columns=df.columns,
            warn=lambda message: self.logger.warning(f"derive {message}"),
        )

    def _build_nested_json_frame(
        self,
        df: pl.DataFrame,
        array_paths: List[str],
        target_columns: List[str],
        root_object: str,
    ) -> pl.DataFrame:
        if df.is_empty():
            return df

        array_path_set = set(array_paths)
        include_columns = target_columns or list(df.columns)
        records: List[Dict[str, Any]] = []

        for row in df.to_dicts():
            nested_row: Dict[str, Any] = {}
            for column in include_columns:
                if column not in row:
                    continue

                value = row[column]
                if root_object and column.startswith(root_object + "."):
                    target_root = nested_row.setdefault(root_object, {})
                    relative_parts = [part for part in column.split(".")[1:] if part]
                    self._set_nested_value(target_root, relative_parts, value, array_path_set, root_object)
                    continue

                parts = [part for part in column.split(".") if part]
                if not parts:
                    continue
                self._set_nested_value(nested_row, parts, value, array_path_set, "")

            records.append(nested_row)

        if not records:
            return df
        return pl.DataFrame(records)

    def _set_nested_value(
        self,
        container: Any,
        parts: List[str],
        value: Any,
        array_paths: set[str],
        base_path: str,
    ) -> None:
        if not parts:
            return

        cursor = container
        consumed: List[str] = []

        for index, part in enumerate(parts):
            is_last = index == len(parts) - 1
            next_part = parts[index + 1] if not is_last else ""
            consumed.append(part)
            absolute_path = ".".join([segment for segment in [base_path, *consumed] if segment])

            if part.isdigit():
                list_index = int(part)
                if not isinstance(cursor, list):
                    return
                while len(cursor) <= list_index:
                    cursor.append(None)
                if is_last:
                    cursor[list_index] = value
                    return
                if cursor[list_index] is None:
                    cursor[list_index] = [] if next_part.isdigit() else {}
                cursor = cursor[list_index]
                continue

            if not isinstance(cursor, dict):
                return

            if is_last:
                cursor[part] = value
                return

            should_be_list = next_part.isdigit() or absolute_path in array_paths
            if part not in cursor or cursor[part] is None:
                cursor[part] = [] if should_be_list else {}

            if should_be_list and not isinstance(cursor[part], list):
                cursor[part] = []
            if not should_be_list and not isinstance(cursor[part], dict):
                cursor[part] = {}

            cursor = cursor[part]

    def _build_file_schema(
        self,
        df: pl.DataFrame,
        file_format: str,
        sample_size: int = 5,
        selected_sheets: Optional[List[str]] = None,
        merged_sheets: bool = False,
    ) -> FileSchema:
        columns: List[SchemaField] = []

        dtypes = getattr(df, "dtypes", [])
        for idx, column_name in enumerate(getattr(df, "columns", [])):
            dtype = dtypes[idx] if idx < len(dtypes) else "Unknown"
            dtype_name = self._dtype_to_string(dtype)
            columns.append(
                SchemaField(
                    name=column_name,
                    dtype=dtype_name,
                    fields=[],
                )
            )

        nested_depth = self._infer_nested_depth(columns)
        selected_sheets = selected_sheets or []

        return FileSchema(
            columns=columns,
            sample_data=df.head(sample_size).to_dicts(),
            nested_depth=nested_depth,
            file_format=file_format,
            total_columns=len(getattr(df, "columns", [])),
            workbook_sheets=selected_sheets,
            selected_sheets=selected_sheets,
            merged_sheets=merged_sheets,
        )

    def _compare_target_schema(
        self,
        output_schema: FileSchema,
        target_schema: Optional[Dict[str, Any]],
    ):
        if not target_schema:
            return None, [], []

        target_fields = self._extract_target_fields(target_schema)
        expected_names = [field["name"] for field in target_fields]
        actual_names = [column.name for column in output_schema.columns]
        missing = [name for name in expected_names if name not in actual_names]
        unexpected = [name for name in actual_names if name not in expected_names]

        return len(missing) == 0 and len(unexpected) == 0, missing, unexpected

    def _extract_source_fields(self, source_schema: Dict[str, Any]) -> List[Dict[str, str]]:
        schema = self._unwrap_schema_payload(source_schema)
        columns = schema.get("columns", [])
        extracted: List[Dict[str, str]] = []

        for column in columns:
            if isinstance(column, str):
                extracted.append({"name": column, "dtype": ""})
            elif isinstance(column, dict):
                name = column.get("name")
                if name:
                    extracted.append({"name": name, "dtype": column.get("dtype", "")})

        return extracted

    def _extract_target_fields(self, target_schema: Dict[str, Any]) -> List[Dict[str, str]]:
        schema = self._unwrap_schema_payload(target_schema)
        extracted: List[Dict[str, str]] = []
        seen = set()

        for column in schema.get("columns", []):
            if isinstance(column, str):
                if column not in seen:
                    extracted.append({"name": column, "description": ""})
                    seen.add(column)
            elif isinstance(column, dict):
                name = column.get("name")
                if name and name not in seen:
                    extracted.append(
                        {
                            "name": name,
                            "description": column.get("description", ""),
                        }
                    )
                    seen.add(name)

        for item in schema.get("items", []):
            mapping = item.get("mapping", {})
            if not isinstance(mapping, dict):
                continue
            for name, details in mapping.items():
                if not name or name in seen:
                    continue
                description = ""
                if isinstance(details, dict):
                    description = details.get("description", "")
                extracted.append({"name": name, "description": description})
                seen.add(name)

        return extracted

    def _unwrap_schema_payload(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not payload:
            return {}

        current = payload
        if isinstance(current, dict) and isinstance(current.get("result"), dict):
            current = current["result"]

        return current if isinstance(current, dict) else {}

    def _score_field_mapping(
        self,
        source_name: str,
        target_name: str,
        target_description: str,
        mapping_hints: List[Dict[str, Any]],
    ) -> Tuple[float, str]:
        for hint in mapping_hints:
            hinted_source = (
                hint.get("source_field")
                or hint.get("source")
                or hint.get("source_column")
            )
            hinted_target = (
                hint.get("target_field")
                or hint.get("target")
                or hint.get("target_column")
            )
            if hinted_source == source_name and hinted_target == target_name:
                return 1.0, "Matched explicit mapping hint"

        normalized_source = self._normalize_identifier(source_name)
        normalized_target = self._normalize_identifier(target_name)
        normalized_description = self._normalize_identifier(target_description)

        if normalized_source == normalized_target:
            return 0.99, "Exact normalized field-name match"

        if normalized_source and normalized_source == normalized_description:
            return 0.96, "Source field matches target description"

        if normalized_source and normalized_source in normalized_target:
            return 0.87, "Source field contained in target field name"

        if normalized_target and normalized_target in normalized_source:
            return 0.84, "Target field name contained in source field"

        source_tokens = set(self._tokenize(source_name))
        target_tokens = set(self._tokenize(target_name))
        description_tokens = set(self._tokenize(target_description))

        shared_name_tokens = source_tokens & target_tokens
        if shared_name_tokens:
            score = 0.6 + min(len(shared_name_tokens), 3) * 0.08
            return min(score, 0.9), (
                "Shared field-name tokens: " + ", ".join(sorted(shared_name_tokens))
            )

        shared_description_tokens = source_tokens & description_tokens
        if shared_description_tokens:
            score = 0.55 + min(len(shared_description_tokens), 3) * 0.08
            return min(score, 0.86), (
                "Shared tokens with target description: "
                + ", ".join(sorted(shared_description_tokens))
            )

        return 0.0, "No meaningful similarity detected"

    def _normalize_identifier(self, value: str) -> str:
        if not value:
            return ""
        tokens = self._tokenize(value)
        return "".join(tokens)

    def _tokenize(self, value: str) -> List[str]:
        if not value:
            return []

        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
        return [token for token in re.split(r"[^A-Za-z0-9]+", expanded.lower()) if token]

    def _resolve_polars_dtype(self, dtype_name: str):
        normalized = (dtype_name or "Utf8").strip().lower()
        mapping = {
            "string": pl.Utf8,
            "utf8": pl.Utf8,
            "text": pl.Utf8,
            "int": pl.Int64,
            "int64": pl.Int64,
            "integer": pl.Int64,
            "float": pl.Float64,
            "float64": pl.Float64,
            "double": pl.Float64,
            "bool": pl.Boolean,
            "boolean": pl.Boolean,
            # Business templates annotate money/quantity columns as "Decimal" and
            # dates as their format string. Without these they fell through to the
            # Utf8 default and the column stayed text with no error raised.
            "decimal": pl.Float64,
            "number": pl.Float64,
            "date": pl.Date,
            "yyyy-mm-dd": pl.Date,
            "datetime": pl.Datetime,
            "timestamp": pl.Datetime,
        }
        if normalized not in mapping:
            # Silently degrading an unknown type to text is how a mistyped rule
            # becomes a data bug nobody sees; say so.
            self.logger.warning(
                f"Unknown cast dtype '{dtype_name}'. Falling back to Utf8. "
                f"Supported: {', '.join(sorted(mapping))}."
            )
        return mapping.get(normalized, pl.Utf8)

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

    def _dtype_to_string(self, dtype: Any) -> str:
        try:
            return str(dtype)
        except Exception:
            return "Unknown"

    def _infer_nested_depth(self, columns: List[SchemaField]) -> int:
        if not columns:
            return 1

        max_depth = 1
        for column in columns:
            dtype = column.dtype.lower()
            if "struct" in dtype or "list" in dtype:
                max_depth = max(max_depth, 2)
        return max_depth

    def _extract_file_id(self, download_url: str) -> str:
        if not download_url:
            return ""
        return download_url.rstrip("/").split("/")[-1]

    def _normalize_file_format(
        self, file_format: str, file_path: Optional[str] = None
    ) -> str:
        if file_format:
            normalized = file_format.lstrip(".").lower()
            if normalized:
                return normalized

        if file_path and "." in file_path:
            return file_path.rsplit(".", 1)[-1].lower()

        return "csv"
