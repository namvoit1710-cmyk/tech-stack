from functools import partial

import polars as pl
import gc
import os
import tempfile
from typing import Dict, List, Optional
from app.layer2_application.interfaces.validator_interface import IValidatorProvider
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.storage_interface import IFileStorageProvider
from app.layer1_domain.entities.validation import ValidationRule, ValidationResult, ODataContent, ODataData
from app.layer4_frameworks.providers.hana import credentials as hana_credentials
from app.layer4_frameworks.providers.hana import hana_reader
from app.layer4_frameworks.providers.validation.semantic_match import (
    SemanticMatcher,
    SemanticRuleError,
    SemanticSpec,
)
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.providers.adaptive_batching import resolve_adaptive_batch_size, resolve_runtime_batch_size
from .rule_handlers import FUZZY_KEYS, RuleFactory, field_path
from app.layer4_frameworks.monitoring.performance_decorator import track_performance

from app.layer4_frameworks.providers.expressions.safe_expression import (
    RuleExpressionError,
)


def _has_semantic_component(rule) -> bool:
    """A composite_unique with a semantic component cannot be built from the frame
    alone - it needs the embedding round trip - so it runs in the semantic pass."""
    for component in (rule.params or {}).get("columns") or []:
        if isinstance(component, dict) and                 str(component.get("match", "")).strip().lower() == "semantic":
            return True
    return False


class PolarsValidatorProvider(IValidatorProvider):
    def __init__(self, logger: ILogger, storage: IFileStorageProvider):
        self.logger = logger
        self.storage = storage
        self.factory = RuleFactory()

    @track_performance
    async def validate_cloud_file(
        self,
        file_path: str,
        file_format: str,
        rules: List[ValidationRule],
        version_id: Optional[str] = None,
        result_mode: str = "errors_only",
        sheet_names: Optional[List[str]] = None,
        header_row: Optional[int] = None,
        field_paths: Optional[Dict[str, str]] = None,
    ) -> ValidationResult:
        try:
            self.logger.info(f"Starting Validation Engine for File: {file_path}")
            # Handlers read this off the rule rather than taking another
            # argument, so the nine of them keep one signature.
            if field_paths:
                for rule in rules:
                    try:
                        rule.field_paths = field_paths
                    except AttributeError:
                        pass
            result_mode = self._normalize_result_mode(result_mode)
            
            # 1. Download & Read via Robust Storage
            self.logger.debug(f"Requesting storage provider to download and parse format: {file_format.upper()}")
            import asyncio
            df_source = await asyncio.to_thread(
                partial(
                    self.storage.download_and_read,
                    file_path,
                    file_format,
                    version_id=version_id,
                    sheet_names=sheet_names,
                    header_row=header_row,
                )
            )
            total_rows = len(df_source)
            self.logger.info(f"Successfully loaded DataFrame into memory. Total Rows: {total_rows}, Total Columns: {len(df_source.columns)}")
            
            # 2. Categorize Rules
            row_rules, global_rules, reference_rules = [], [], []
            assignment_rules, semantic_rules = [], []
            for r in rules:
                if r.type == 'composite_unique' and _has_semantic_component(r):
                    semantic_rules.append(r)
                elif r.type in {'unique', 'set_unique', 'composite_unique'}:
                    global_rules.append(r)
                elif r.type in {'assignment', 'filter'}:
                    # A Filter Rule is the same source -> target question as an
                    # Assignment Rule, asked the other way round: not "is this
                    # the derived value" but "is this value permitted at all".
                    assignment_rules.append(r)
                elif r.type == 'reference_lookup':
                    reference_rules.append(r)
                elif r.type == 'semantic_unique':
                    # Needs a database round trip before it has anything to express,
                    # like reference_lookup - so it is not a row handler.
                    semantic_rules.append(r)
                else:
                    row_rules.append(r)

            self.logger.debug(
                f"Categorized Rules - Row-level: {len(row_rules)}, "
                f"Global: {len(global_rules)}, Reference: {len(reference_rules)}"
            )

            # 3. Parse Row Rules
            row_expressions, error_map = [], {}
            for idx, rule in enumerate(row_rules):
                handler = self.factory.get_handler(rule.type)
                if handler:
                    alias = f"err_{idx}"
                    self.logger.debug(f"Compiling Row Rule [{idx+1}/{len(row_rules)}]: '{rule.rule_name}' (Type: {rule.type}) -> Alias: {alias}")
                    try:
                        exprs = handler.parse_rule(rule, alias)
                    except RuleExpressionError as exc:
                        # Surface it, rather than validating everything else and
                        # reporting success for a rule that never ran.
                        raise ValueError(str(exc)) from exc
                    row_expressions.extend(exprs)
                    error_map[alias] = rule
                    # A handler may report per column, so that the violation names
                    # the field rather than just the rule. Register whatever
                    # aliases it actually produced instead of special-casing one
                    # rule type -- a handler added later would otherwise emit
                    # columns nothing maps back to a rule.
                    for expression in exprs:
                        try:
                            produced = expression.meta.output_name()
                        except Exception:
                            continue
                        if produced:
                            error_map[produced] = rule
                else:
                    self.logger.warning(f"No handler found for rule type: {rule.type}. Skipping rule: {rule.rule_name}")

            # 4. Global Pass (Unique)
            if global_rules:
                self.logger.info(f"Executing Global Pass for {len(global_rules)} global rules...")
                global_exprs, global_map = self._process_global_rules(df_source, global_rules)
                row_expressions.extend(global_exprs)
                error_map.update(global_map)

            # 4b. Reference Pass (reference_lookup) — vectorized membership against a
            # materialized key-set (e.g. dangerous substances). One hash lookup per
            # cell, O(n + m), instead of an O(n * m) per-cell scan of the reference.
            if assignment_rules:
                self.logger.info(
                    f"Executing Assignment Pass for {len(assignment_rules)} rules..."
                )
                assign_exprs, assign_map = self._process_assignment_rules(
                    assignment_rules, df_source
                )
                row_expressions.extend(assign_exprs)
                error_map.update(assign_map)

            if reference_rules:
                self.logger.info(
                    f"Executing Reference Pass for {len(reference_rules)} reference_lookup rules..."
                )
                ref_exprs, ref_map = self._process_reference_rules(
                    reference_rules, df_source
                )
                row_expressions.extend(ref_exprs)
                error_map.update(ref_map)

            # 4c. Semantic Pass (semantic_unique) - the only pass that leaves this
            # process. Embeddings are compared in HANA, and only the values that
            # matched come back, so the corpus is never pulled across.
            if semantic_rules:
                self.logger.info(
                    f"Executing Semantic Pass for {len(semantic_rules)} semantic_unique rules..."
                )
                sem_exprs, sem_map = self._process_semantic_rules(
                    semantic_rules, df_source
                )
                row_expressions.extend(sem_exprs)
                error_map.update(sem_map)

            # 5. Execute Validation (with batching & streaming writer)
            if not row_expressions:
                self.logger.info("No valid rule expressions compiled. Returning fully valid result.")
                return ValidationResult(total_rows=total_rows, valid_rows=total_rows, invalid_rows=0, odata=ODataContent(data=[]))

            writer = _StreamingResultWriter(self.logger, result_mode=result_mode)
            invalid_rows_count = 0
            error_summary = {}
            batch_size = resolve_adaptive_batch_size(
                df_source,
                total_rows,
                self.logger,
                workload="validation",
            )
            self.logger.info(f"Applying expressions with dynamic batches (Initial Batch Size: {batch_size})...")

            cursor = 0
            batch_num = 0
            while cursor < total_rows:
                await asyncio.sleep(0)  # Yield control back to main event loop to prevent starvation
                batch_num += 1
                if (batch_num - 1) % max(1, settings.ADAPTIVE_BATCH_REEVALUATE_EVERY_N_BATCHES) == 0:
                    remaining_rows = total_rows - cursor
                    batch_size = resolve_runtime_batch_size(
                        df_source,
                        remaining_rows,
                        batch_size,
                        self.logger,
                        workload="validation",
                    )
                end_row = min(cursor + batch_size, total_rows)
                self.logger.info(
                    f"Processing Batch {batch_num} [Rows {cursor} to {end_row}] (Batch Size: {batch_size})"
                )
                
                batch = df_source.slice(cursor, batch_size)
                
                # Run the heavy Polars computation in a thread so it does NOT block
                # the event loop. This is critical: without this, the keep-alive
                # generator in the controller cannot yield spaces while Polars is
                # crunching, causing the connection to be dropped by GoRouter/browser.
                batch_res = await asyncio.to_thread(batch.with_columns, row_expressions)
                
                valid_error_cols = [c for c in error_map.keys() if c in batch_res.columns]
                
                if valid_error_cols:
                    has_error = batch_res.select(pl.any_horizontal([pl.col(c).is_not_null() for c in valid_error_cols])).to_series()
                    batch_invalid = has_error.sum()
                    invalid_rows_count += batch_invalid
                    
                    if batch_invalid > 0:
                        self.logger.info(f"Batch {batch_num} caught {batch_invalid} rows with errors.")
                    
                    for col in valid_error_cols:
                        cnt = batch_res.select(pl.col(col).count()).item()
                        if cnt > 0:
                            if col not in error_summary:
                                error_summary[col] = {"rule": error_map[col], "count": 0}
                            error_summary[col]["count"] += cnt
                            
                    await asyncio.to_thread(writer.write, batch_res, valid_error_cols)
                
                del batch, batch_res
                gc.collect()
                cursor = end_row

            self.logger.info(f"Validation Execution Finished. Total Invalid Rows: {invalid_rows_count}")

            # 6. Upload Results using Robust Storage
            result_url = ""
            result_file_id = ""
            result_version_id = None
            if writer.has_content:
                self.logger.info("Uploading processed CSV result file to cloud storage...")
                try:
                    uploaded_result = await asyncio.to_thread(
                        self.storage.upload_file,
                        writer.file_path,
                        file_format="csv",
                        filename=self._build_validation_result_filename(file_path, "csv"),
                    )
                    result_url = uploaded_result.download_url
                    result_file_id = uploaded_result.file_id
                    result_version_id = uploaded_result.version_id or None
                except Exception as upload_e:
                    self.logger.error(f"Failed to upload result file: {upload_e}. Continuing without result file.")
            writer.cleanup()

            # Map to Exact Required OData JSON Spec
            self.logger.debug("Formatting final OData validation summary...")
            summary_list = []
            for alias, info in error_summary.items():
                rule_obj = info['rule']
                # Extracts the actual column array to map it to the alias for "error_code"
                affected_cols = rule_obj.params.get('columns', [])
                summary_list.append(ODataData(
                    rule_name=rule_obj.rule_name,
                    violate=info['count'],
                    error_message=rule_obj.error_message,
                    error_code={alias: affected_cols} # <--- THIS MATCHES YOUR EXACT JSON FORMAT REQUEST
                ))

            self.logger.info("Successfully constructed DataValidationResult payload.")
            return ValidationResult(
                total_rows=total_rows, 
                valid_rows=total_rows - invalid_rows_count, 
                invalid_rows=invalid_rows_count, 
                odata=ODataContent(
                    data=summary_list,
                    result_file_url=result_url,
                    result_file_id=result_file_id,
                    result_version_id=result_version_id,
                )
            )

        except Exception as e:
            self.logger.error(f"Validation Engine Hard Crash: {e}")
            raise e

    def _process_global_rules(self, df: pl.DataFrame, global_rules: List[ValidationRule]):
        exprs = []
        mapping = {}
        for rule_index, rule in enumerate(global_rules):
            if rule.type == 'unique':
                unique_exprs, unique_mapping = self._build_unique_rule_expressions(df, rule)
                exprs.extend(unique_exprs)
                mapping.update(unique_mapping)
            elif rule.type == 'set_unique':
                columns = self._validate_set_unique_columns(df, rule)
                alias = f"setuniq_{rule_index}"
                duplicate_keys = self._find_duplicate_composite_keys(df, columns)
                if duplicate_keys:
                    self.logger.debug(
                        f"set_unique Rule '{rule.rule_name}': Found {len(duplicate_keys)} duplicate composite keys for columns {columns}."
                    )
                    exprs.append(
                        pl.when(self._build_composite_duplicate_condition(columns, duplicate_keys)).then(
                            pl.struct([
                                pl.lit(rule.rule_name).alias("rule"),
                                pl.lit(", ".join(columns)).alias("field"),
                                pl.lit(", ".join(field_path(rule, c) for c in columns)).alias("path"),
                                pl.lit(rule.error_message).alias("message"),
                                pl.concat_str([
                                    pl.col(column).cast(pl.Utf8, strict=False).fill_null("null")
                                    for column in columns
                                ], separator=" | ").alias("value")
                            ])
                        ).otherwise(None).alias(alias)
                    )
                    mapping[alias] = rule
            elif rule.type == 'composite_unique':
                composite_exprs, composite_mapping = self._build_composite_unique(
                    df, rule, rule_index
                )
                exprs.extend(composite_exprs)
                mapping.update(composite_mapping)
            else:
                self.logger.warning(f"Unsupported global rule type: {rule.type}. Skipping rule: {rule.rule_name}")
        return exprs, mapping

    def _build_composite_unique(self, df: pl.DataFrame, rule, rule_index: int,
                                semantic_keys=None):
        """An AND of several fields where each may match exactly or loosely.

        This is the shape SMDG's duplication config actually uses and the one
        nothing here could express. A `DuplicationCheck` group ANDs its fields and
        each field carries its own `isFuzzySearch`, so a real rule reads:

            organizationBPName1  fuzzy
        AND streetName           fuzzy
        AND cityName, country, postalCode, region   exact

        `set_unique` compares a composite key exactly; `fuzzy_unique` compares one
        column loosely. Neither expresses the mixture, and 6 of the 15 configured
        groups on the landscape inspected are exactly this - so the rules were
        being dropped rather than approximated, which is the right failure but not
        a useful one.

        The implementation costs what `set_unique` costs: each component becomes a
        key expression - raw for exact, the `fuzzy_unique` blocking key for fuzzy -
        and the tuple is checked with `is_duplicated()`. O(n), no pairwise compare.

        Fuzzy here is the same approximation `fuzzy_unique` already makes: HANA
        matches with CONTAINS(..., FUZZY(0.8)), a similarity threshold, while this
        collapses formatting and compares. Close, not identical, and stated rather
        than implied.

        A row missing ANY component is not a duplicate of another row missing the
        same one: absent values are `required`'s business, and two half-empty rows
        are not evidence of the same object.
        """
        components = rule.params.get('columns') or []
        keys, names, present = [], [], None
        for component in components:
            if isinstance(component, str):
                column, match = component, 'exact'
            else:
                column = component.get('column')
                match = str(component.get('match', 'exact')).strip().lower()
            if column not in df.columns:
                raise ValueError(
                    f"composite_unique rule '{rule.rule_name}': column '{column}' "
                    "is not in the file"
                )
            if match in ('fuzzy', 'normalized'):
                key = FUZZY_KEYS['normalized'](column)
            elif match == 'exact':
                key = pl.col(column).cast(pl.Utf8, strict=False).str.strip_chars()
            elif match == 'semantic' and semantic_keys is not None:
                # The value keys on WHICH near-duplicate cluster it fell into, so
                # the composite comparison stays the same tuple equality. A value
                # with no match maps to itself and can only tie with an identical
                # one - what an exact match would have said anyway.
                mapping = semantic_keys.get(column) or {}
                key = (pl.col(column).cast(pl.Utf8, strict=False).str.strip_chars()
                       .replace_strict(mapping, default=None)
                       .fill_null(pl.col(column).cast(pl.Utf8, strict=False)
                                  .str.strip_chars()))
            elif match == 'semantic':
                # Reached the frame-only builder, which means the rule was not
                # routed through the semantic pass. Falling back to an exact key
                # would quietly turn a semantic match into a strict one and
                # report fewer duplicates than the template asks for.
                raise ValueError(
                    f"composite_unique rule '{rule.rule_name}': the semantic "
                    f"component '{column}' has no resolved clusters. This rule "
                    "must run in the semantic pass."
                )
            else:
                raise ValueError(
                    f"composite_unique rule '{rule.rule_name}': match '{match}' on "
                    f"'{column}' is not exact, fuzzy or semantic."
                )
            filled = (pl.col(column).is_not_null()
                      & (pl.col(column).cast(pl.Utf8, strict=False).str.strip_chars() != ""))
            present = filled if present is None else (present & filled)
            keys.append(key.fill_null(""))
            names.append(column)

        if not keys:
            return [], {}

        composite = pl.concat_str(keys, separator="")
        alias = f"compuniq_{rule_index}"
        violation = present & composite.is_duplicated()
        expression = pl.when(violation).then(
            pl.struct([
                pl.lit(rule.rule_name).alias("rule"),
                pl.lit(", ".join(names)).alias("field"),
                pl.lit(", ".join(field_path(rule, c) for c in names)).alias("path"),
                pl.lit(rule.error_message).alias("message"),
                pl.concat_str([
                    pl.col(c).cast(pl.Utf8, strict=False).fill_null("null") for c in names
                ], separator=" | ").alias("value"),
            ])
        ).otherwise(None).alias(alias)
        return [expression], {alias: rule}

    def _build_unique_rule_expressions(
        self,
        df: pl.DataFrame,
        rule: ValidationRule,
    ):
        exprs = []
        mapping = {}
        columns = rule.params.get('columns', [])
        if not isinstance(columns, list):
            return exprs, mapping

        for column_name in columns:
            if column_name not in df.columns:
                continue

            duplicate_values = (
                df.filter(pl.col(column_name).is_duplicated())
                .select(column_name)
                .unique()
                .to_series()
                .to_list()
            )
            if duplicate_values:
                self.logger.debug(
                    f"Unique Rule '{rule.rule_name}': Found {len(duplicate_values)} duplicate values in column '{column_name}'."
                )
                alias = f"uniq_{column_name}"
                exprs.append(
                    pl.when(pl.col(column_name).is_in(list(duplicate_values))).then(
                        pl.struct([
                            pl.lit(rule.rule_name).alias("rule"),
                            pl.lit(column_name).alias("field"),
                            pl.lit(field_path(rule, column_name)).alias("path"),
                            pl.lit(rule.error_message).alias("message"),
                            pl.col(column_name).cast(pl.Utf8).alias("value")
                        ])
                    ).otherwise(None).alias(alias)
                )
                mapping[alias] = rule
        return exprs, mapping

    def _validate_set_unique_columns(self, df: pl.DataFrame, rule: ValidationRule) -> List[str]:
        columns = rule.params.get('columns')
        if not isinstance(columns, list) or not columns:
            raise ValueError(f"Rule '{rule.rule_name}' must define params.columns as a non-empty list")

        invalid_columns = [column for column in columns if not isinstance(column, str) or not column.strip()]
        if invalid_columns:
            raise ValueError(f"Rule '{rule.rule_name}' has invalid column names in params.columns")

        missing_columns = [column for column in columns if column not in df.columns]
        if missing_columns:
            raise ValueError(
                f"Rule '{rule.rule_name}' references missing columns: {', '.join(missing_columns)}"
            )

        return columns

    def _find_duplicate_composite_keys(self, df: pl.DataFrame, columns: List[str]) -> List[dict]:
        duplicate_rows = (
            df.group_by(columns)
            .len()
            .filter(pl.col('len') > 1)
            .select(columns)
        )
        return duplicate_rows.to_dicts()

    def _build_composite_duplicate_condition(self, columns: List[str], duplicate_keys: List[dict]) -> pl.Expr:
        conditions = []
        for duplicate_key in duplicate_keys:
            condition = None
            for column in columns:
                value = duplicate_key[column]
                column_condition = pl.col(column).is_null() if value is None else pl.col(column) == value
                condition = column_condition if condition is None else condition & column_condition
            if condition is not None:
                conditions.append(condition)

        if not conditions:
            return pl.lit(False)

        composite_condition = conditions[0]
        for extra_condition in conditions[1:]:
            composite_condition = composite_condition | extra_condition
        return composite_condition

    def _normalize_result_mode(self, result_mode: Optional[str]) -> str:
        normalized = (result_mode or "errors_only").strip().lower()
        aliases = {
            "errors_only": "errors_only",
            "error_rows": "errors_only",
            "errors": "errors_only",
            "full_file": "full_file",
            "full_rows": "full_file",
            "all_rows": "full_file",
            "full": "full_file",
        }
        if normalized not in aliases:
            raise ValueError("result_mode must be one of: errors_only, full_file")
        return aliases[normalized]

    def _build_validation_result_filename(self, source_file_id: str, file_format: str) -> str:
        if not file_format.startswith("."):
            file_format = f".{file_format}"
        return f"edited_validation_datafactory{file_format}"

    def _process_assignment_rules(
        self, assignment_rules: List[ValidationRule], df_source=None
    ):
        """Build violation expressions for ``assignment`` rules.

        An Assignment Rule in the template says a source field determines a
        target field — articleType decides valuationClass, purchasingValueKey
        decides the reminder days, netPriceAmount decides effectivePrice. What
        the template screen does *not* show is the value mapping behind it, so
        the mapping arrives here as data: a two-column file of
        source value -> target value.

        Two readings of "determines", both of which the template supports, and
        which one applies is a property of the rule rather than something to
        guess:

        - ``match``   the target must equal the value the mapping gives for that
                      source. One right answer.
        - ``allowed`` the target must be one of the values the mapping lists for
                      that source. A constrained list, not a single answer.

        A source value the mapping has never heard of is not judged here — that
        is a reference_lookup question about the source, and failing it in two
        places would report one problem twice.
        """
        exprs = []
        mapping = {}
        for idx, rule in enumerate(assignment_rules):
            params = rule.params or {}
            source_column = params.get("source_column")
            target_column = params.get("target_column")
            mapping_source = params.get("mapping_source")
            is_filter = rule.type == "filter"
            # A Filter Rule is always a permitted-set question; `match` would
            # mean the mapping names one right answer, which a filter never does.
            mode = "allowed" if is_filter else str(
                params.get("mode") or "match"
            ).strip().lower()

            if not (source_column and target_column and mapping_source):
                self.logger.warning(
                    f"{'Filter' if is_filter else 'Assignment'} rule "
                    f"'{rule.rule_name}' needs source_column, "
                    "target_column and mapping_source; skipping."
                )
                continue

            if df_source is not None:
                missing = [c for c in (source_column, target_column)
                           if c not in df_source.columns]
                if missing:
                    self.logger.warning(
                        f"Assignment rule '{rule.rule_name}' names column(s) "
                        f"{missing} which the file does not have; skipping."
                    )
                    continue

            try:
                mapping_df = self.storage.download_and_read(
                    mapping_source, params.get("mapping_format", "csv")
                )
            except Exception as exc:
                self.logger.error(
                    f"Assignment rule '{rule.rule_name}': could not load mapping "
                    f"'{mapping_source}': {exc}. Skipping rule."
                )
                continue

            map_source = params.get("mapping_source_column") or source_column
            map_target = params.get("mapping_target_column") or target_column
            if map_source not in mapping_df.columns or map_target not in mapping_df.columns:
                self.logger.error(
                    f"Assignment rule '{rule.rule_name}': mapping needs columns "
                    f"'{map_source}' and '{map_target}'; it has "
                    f"{mapping_df.columns}. Skipping rule."
                )
                continue

            # Whether a column is compared as a number is a property of the
            # data, not of the rule: both sides have to read as numbers, or the
            # text form is the only thing they share.
            src_numeric = (self._reads_as_number(df_source, source_column)
                           and self._reads_as_number(mapping_df, map_source))
            tgt_numeric = (self._reads_as_number(df_source, target_column)
                           and self._reads_as_number(mapping_df, map_target))

            pairs = (
                mapping_df.select([
                    self._match_key(map_source, src_numeric).alias("__src"),
                    self._match_key(map_target, tgt_numeric).alias("__tgt"),
                ])
                .drop_nulls()
                .unique()
            )
            if pairs.is_empty():
                self.logger.warning(
                    f"Assignment rule '{rule.rule_name}': mapping is empty; skipping."
                )
                continue

            source_norm = self._match_key(source_column, src_numeric)
            #: what the comparison uses...
            target_key = self._match_key(target_column, tgt_numeric)
            #: ...and what the violation reports, which stays as written.
            target_text = pl.col(target_column).cast(pl.Utf8)

            if mode == "allowed":
                # Many targets per source: compare the (source, target) pair.
                allowed_pairs = (
                    pairs.select(
                        (pl.col("__src") + pl.lit(chr(31)) + pl.col("__tgt"))
                        .alias("__pair")
                    )["__pair"].to_list()
                )
                known_sources = pairs["__src"].unique().to_list()
                combined = source_norm + pl.lit(chr(31)) + target_key
                violated = (
                    source_norm.is_in(known_sources)
                    & pl.col(target_column).is_not_null()
                    & ~combined.is_in(allowed_pairs)
                )
                expected = pl.lit("one of the values mapped for this source")
            else:
                # One target per source: look the expected value up.
                lookup = dict(zip(pairs["__src"].to_list(), pairs["__tgt"].to_list()))
                expected = source_norm.replace_strict(
                    lookup, default=None, return_dtype=pl.Utf8
                )
                violated = (
                    expected.is_not_null()
                    & pl.col(target_column).is_not_null()
                    & (target_key != expected)
                )

            alias = f"{'filter' if is_filter else 'assign'}_{idx}"
            fields = [
                pl.lit(rule.rule_name).alias("rule"),
                pl.lit(target_column).alias("field"),
                pl.lit(field_path(rule, target_column)).alias("path"),
                pl.lit(rule.error_message).alias("message"),
                target_text.alias("value"),
            ]
            if is_filter:
                # The requirement names the source as well as the target: an
                # invalid supplier is only invalid *for the chosen purchasing
                # organisation*, and an error that omits the source cannot say
                # which of the two fields the user should change.
                fields.extend([
                    pl.lit("FILTER").alias("ruleType"),
                    pl.lit(source_column).alias("sourceField"),
                    pl.lit(field_path(rule, source_column)).alias("sourcePath"),
                    pl.lit(str(params.get("severity") or "ERROR").upper())
                    .alias("severity"),
                ])
            struct = pl.struct(fields)
            exprs.append(
                pl.when(violated.fill_null(False)).then(struct).otherwise(None)
                .alias(alias)
            )
            mapping[alias] = rule

        return exprs, mapping

    def _process_reference_rules(
        self, reference_rules: List[ValidationRule], df_source=None
    ):
        """
        Build violation expressions for ``reference_lookup`` rules.

        A reference rule flags cells whose (normalized) value is present in — or,
        when ``violate_when='not_in_set'``, absent from — a materialized reference
        key-set. The key-set is loaded ONCE per rule (typically a small parquet of
        distinct normalized keys), then each target column is checked with a single
        vectorized ``is_in`` (hash membership) — O(n + m), not O(n * m).

        Rule params:
          - columns:           list of user-file columns to check
          - reference_source:  file id / path of the reference key-set (or reference_id)
          - reference_format:  format of the reference (default "parquet")
          - key_column:        column in the reference holding the keys (default "key")
          - violate_when:      "in_set" (default) or "not_in_set"

        Compound mode (a Filter Rule: "is this target valid for that source?"):
          - source_columns:    ordered columns forming the composite key on the
                               user file, e.g. ["purchasingOrganization", "supplier"]
          - key_columns:       the matching columns on the reference (defaults to
                               ``source_columns``)
          - field:             which column the error is reported against
                               (defaults to the last of ``source_columns``)
          - separator:         composite-key joiner (defaults to a unit separator,
                               so ordinary data cannot forge a key boundary)

        A Filter Rule constrains a PAIR, not two independent cells: "AU" may be a
        valid country and "NRW" a valid region while ("AU","NRW") is still wrong.
        Single-column mode cannot express that; compound mode checks the tuple.
        """
        exprs = []
        mapping = {}
        for idx, r in enumerate(reference_rules):
            params = r.params or {}
            ref_source = params.get('reference_source') or params.get('reference_id')
            ref_format = params.get('reference_format', 'parquet')
            key_column = params.get('key_column', 'key')
            columns = params.get('columns', [])
            violate_when = params.get('violate_when', 'in_set')

            source_columns = params.get('source_columns') or []

            if not ref_source or not (columns or source_columns):
                self.logger.warning(
                    f"Reference rule '{r.rule_name}' missing reference_source and "
                    "columns/source_columns; skipping."
                )
                continue

            try:
                ref_df = self.storage.download_and_read(ref_source, ref_format)
            except Exception as e:
                self.logger.error(
                    f"Reference rule '{r.rule_name}': failed to load reference "
                    f"'{ref_source}' ({ref_format}): {e}. Skipping rule."
                )
                continue

            if source_columns:
                frame_columns = set(df_source.columns) if df_source is not None else None
                expression, alias = self._compound_reference_expr(
                    r, idx, ref_df, params, frame_columns
                )
                if expression is not None:
                    exprs.append(expression)
                    mapping[alias] = r
                continue

            if key_column not in ref_df.columns:
                key_column = ref_df.columns[0] if ref_df.columns else None
            if not key_column:
                self.logger.warning(
                    f"Reference rule '{r.rule_name}': reference has no columns; skipping."
                )
                continue

            # Normalize the key-set the same way we normalize the user cells:
            # lower-case + trim. (Materialization should already normalize, but we
            # normalize here too so the rule is correct against any reference.)
            key_series = (
                ref_df.select(
                    pl.col(key_column).cast(pl.Utf8).str.strip_chars().str.to_lowercase()
                )
                .drop_nulls()
                .to_series()
            )
            self.logger.info(
                f"Reference rule '{r.rule_name}': loaded {key_series.len()} keys "
                f"from '{key_column}' of reference '{ref_source}'."
            )

            frame_columns = set(df_source.columns) if df_source is not None else None
            missing = (
                [c for c in columns if c not in frame_columns]
                if frame_columns is not None
                else []
            )
            if missing:
                self.logger.warning(
                    f"Reference rule '{r.rule_name}': column(s) {missing} are not on "
                    "the file; skipping rule."
                )
                continue

            for col in columns:
                alias = f"ref_{idx}_{col}"
                norm = pl.col(col).cast(pl.Utf8).str.strip_chars().str.to_lowercase()
                cond = norm.is_in(key_series)
                if violate_when == 'not_in_set':
                    cond = cond.not_()
                struct = pl.struct([
                    pl.lit(r.rule_name).alias("rule"),
                    pl.lit(col).alias("field"),
                    pl.lit(field_path(r, col)).alias("path"),
                    pl.lit(r.error_message).alias("message"),
                    pl.col(col).cast(pl.Utf8).alias("value"),
                ])
                exprs.append(pl.when(cond).then(struct).otherwise(None).alias(alias))
                mapping[alias] = r
        return exprs, mapping

    @staticmethod
    def _normalized(column: str) -> "pl.Expr":
        """Cells and reference keys must be normalized identically or every
        comparison silently misses on case or padding."""
        return pl.col(column).cast(pl.Utf8).str.strip_chars().str.to_lowercase()

    @staticmethod
    def _reads_as_number(frame, column: str) -> bool:
        """True when the column holds numbers and nothing is lost by saying so.

        The dtype alone cannot decide this: the CSV reader hands every column
        over as text, so both sides of a comparison are strings even when the
        values are plainly numeric. The question that matters is whether reading
        them as numbers is lossless.

        For ``8`` and ``8.0`` it is -- they are one number written two ways, and
        a CSV drops the trailing zero of a whole float, so text comparison would
        silently never match. For an SAP code like ``0001`` it is not: the
        leading zero carries meaning, and dropping it would make ``0001`` and
        ``1`` the same key and quietly merge two purchasing organisations. So a
        leading zero before another digit disqualifies the column outright.
        """
        if frame is None or column not in getattr(frame, "columns", []):
            return False
        try:
            series = frame[column]
            if series.len() == 0:
                return False
            if series.dtype.is_numeric():
                return True
            text = series.cast(pl.Utf8, strict=False).str.strip_chars()
            if text.str.contains(r"^[+-]?0[0-9]").any():
                return False
            return text.null_count() == text.cast(pl.Float64, strict=False).null_count()
        except Exception:
            return False

    @classmethod
    def _match_key(cls, column: str, numeric: bool) -> "pl.Expr":
        """The form both sides of a comparison are reduced to.

        For numbers that is the value, not its spelling. A whole float loses its
        trailing zero on the way through CSV -- polars writes ``8.0`` as ``8`` --
        so a mapping file carrying ``8.0`` would never match a column carrying
        ``8``, and the rule would report either nothing at all or every row,
        depending on which side of the comparison the number sat.
        """
        if numeric:
            return pl.col(column).cast(pl.Float64, strict=False).cast(pl.Utf8)
        return cls._normalized(column)

    def _compound_reference_expr(
        self,
        rule: ValidationRule,
        idx: int,
        reference_df,
        params: dict,
        frame_columns,
    ):
        """One violation expression for a compound (multi-column) reference rule."""
        source_columns = [str(c).strip() for c in params.get('source_columns') or [] if str(c).strip()]
        key_columns = [
            str(c).strip() for c in (params.get('key_columns') or source_columns) if str(c).strip()
        ]
        separator = params.get('separator', '\x1f')
        violate_when = params.get('violate_when', 'not_in_set')

        if frame_columns is not None:
            missing = [c for c in source_columns if c not in frame_columns]
            if missing:
                self.logger.warning(
                    f"Reference rule '{rule.rule_name}': source column(s) {missing} are "
                    "not on the file; skipping rule."
                )
                return None, None

        missing_reference = [c for c in key_columns if c not in reference_df.columns]
        if missing_reference:
            self.logger.warning(
                f"Reference rule '{rule.rule_name}': reference is missing key column(s) "
                f"{missing_reference}; skipping rule."
            )
            return None, None

        key_series = (
            reference_df.select(
                pl.concat_str(
                    [self._normalized(c) for c in key_columns],
                    separator=separator,
                    ignore_nulls=False,
                ).alias("__compound_key")
            )
            .drop_nulls()
            .to_series()
        )
        self.logger.info(
            f"Reference rule '{rule.rule_name}': loaded {key_series.len()} compound keys "
            f"over {key_columns}."
        )

        composite = pl.concat_str(
            [self._normalized(c) for c in source_columns],
            separator=separator,
            ignore_nulls=False,
        )
        condition = composite.is_in(key_series)
        if violate_when == 'not_in_set':
            condition = condition.not_()

        # A row that has not filled the pair in yet is incomplete, not invalid;
        # `required` is the rule that owns that complaint.
        any_null = pl.any_horizontal([pl.col(c).is_null() for c in source_columns])
        condition = condition & any_null.not_()

        field = params.get('field') or source_columns[-1]
        struct = pl.struct([
            pl.lit(rule.rule_name).alias("rule"),
            pl.lit(field).alias("field"),
            pl.lit(field_path(rule, field)).alias("path"),
            pl.lit(rule.error_message).alias("message"),
            pl.col(field).cast(pl.Utf8).alias("value"),
        ])
        alias = f"ref_{idx}_{field}"
        return pl.when(condition).then(struct).otherwise(None).alias(alias), alias

    def _composite_with_semantic(self, rule, rule_index: int, df_source):
        """A composite AND where one or more components match by meaning.

        This is the rule shape SMDG's config implies but cannot store, and the one
        the dev duplication rule for a packaging template is written in:

            Manufacturer Part Number  exact
        AND Manufacturer Name         exact
        AND Product Description       semantic

        Running the two rule types separately would OR them, not AND them, and
        report far more than the template asks for. So the semantic component is
        resolved to a cluster key first and then joins the same tuple comparison
        the exact and fuzzy components already use.

        Each semantic component carries its own `reference`/`threshold`, because
        two fields in one group can reasonably want different thresholds.
        """
        params = rule.params or {}
        semantic_keys = {}
        for component in params.get("columns") or []:
            if not isinstance(component, dict):
                continue
            if str(component.get("match", "")).strip().lower() != "semantic":
                continue
            column = component.get("column")
            if df_source is not None and column not in df_source.columns:
                raise SemanticRuleError(
                    f"rule '{rule.rule_name}' (composite_unique): column "
                    f"'{column}' is not in the file"
                )
            spec = SemanticSpec.parse({
                "columns": [column],
                **{k: component[k] for k in ("threshold", "min_length", "model",
                                             "reference") if k in component},
            })
            matcher = SemanticMatcher(spec, self._semantic_executor(rule, spec))
            values = df_source[column].to_list() if df_source is not None else []
            semantic_keys[column] = matcher.clusters(values)
            for warning in matcher.warnings:
                self.logger.warning(f"{rule.rule_name}: {warning}")

        return self._build_composite_unique(
            df_source, rule, rule_index, semantic_keys=semantic_keys
        )

    def _semantic_executor(self, rule, spec):
        """A `(sql, params) -> rows` callable against the HANA the RULE names.

        `semantic_executor` on the provider wins if one was injected - that is how a
        test, or a caller that already holds a pooled connection, avoids dialling a
        second one. Otherwise the connection is opened from `reference.connection`:
        the payload says which host, port, user and schema, and DF supplies only the
        password, from the binding or its own config. DF never chooses the database.
        """
        injected = getattr(self, "semantic_executor", None)
        if injected is not None:
            return injected

        conn_spec = spec.reference.connection if spec.reference else None
        if conn_spec is None:
            raise SemanticRuleError(
                f"rule '{rule.rule_name}' (semantic_unique): needs "
                "'reference.connection' naming which HANA to compare against, or an "
                "executor supplied by the caller. Embeddings are computed in the "
                "database, so there is nowhere else for this to run."
            )

        password, source = hana_credentials.resolve(
            conn_spec.host, conn_spec.user, conn_spec.password
        )
        self.logger.info(
            f"{rule.rule_name}: semantic reference on {conn_spec.user}@"
            f"{conn_spec.host}, credential from {source}"
        )

        def execute(sql, params):
            connection = hana_reader.connect(
                host=conn_spec.host, port=conn_spec.port, user=conn_spec.user,
                password=password, encrypt=conn_spec.encrypt,
                validate_cert=conn_spec.validate_cert,
            )
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(sql, list(params))
                    return cursor.fetchall()
                finally:
                    cursor.close()
            finally:
                connection.close()

        return execute

    def _process_semantic_rules(
        self, semantic_rules: List[ValidationRule], df_source=None
    ):
        """Build violation expressions for ``semantic_unique`` rules.

        The shape is reference_lookup's: resolve a set of offending values once,
        then check each column with one vectorized ``is_in``. What differs is where
        the set comes from - a cosine comparison in HANA rather than a parquet of
        keys - and that the match itself is reported, because "this looks like
        something else" is only actionable if you can see what.

        A rule whose query fails is NOT skipped. A duplication check that silently
        returns nothing is indistinguishable from a clean file, and that is the one
        outcome worse than an error.
        """
        exprs, mapping = [], {}
        for idx, rule in enumerate(semantic_rules):
            if rule.type == 'composite_unique':
                composite_exprs, composite_map = self._composite_with_semantic(
                    rule, idx, df_source
                )
                exprs.extend(composite_exprs)
                mapping.update(composite_map)
                continue

            try:
                spec = SemanticSpec.parse(rule.params or {})
            except SemanticRuleError as exc:
                raise SemanticRuleError(f"rule '{rule.rule_name}': {exc}") from exc

            execute = self._semantic_executor(rule, spec)
            matcher = SemanticMatcher(spec, execute)

            for column in spec.columns:
                if df_source is not None and column not in df_source.columns:
                    raise SemanticRuleError(
                        f"rule '{rule.rule_name}' (semantic_unique): column "
                        f"'{column}' is not in the file"
                    )
                values = df_source[column].to_list() if df_source is not None else []
                found = matcher.find(values)
                for warning in matcher.warnings:
                    self.logger.warning(f"{rule.rule_name}: {warning}")
                matcher.warnings.clear()
                if not found:
                    continue

                alias = f"sem_{idx}_{column}"
                # The match and the score ride along in the violation, so the report
                # says WHAT it resembles rather than only that it resembles something.
                score_map = {v: f"{m.matched} ({m.score:.4f})" for v, m in found.items()}
                key = pl.col(column).cast(pl.Utf8).str.strip_chars()
                struct = pl.struct([
                    pl.lit(rule.rule_name).alias("rule"),
                    pl.lit(column).alias("field"),
                    pl.lit(field_path(rule, column)).alias("path"),
                    pl.lit(rule.error_message).alias("message"),
                    key.replace_strict(score_map, default=None).alias("value"),
                ])
                exprs.append(
                    pl.when(key.is_in(list(found))).then(struct).otherwise(None).alias(alias)
                )
                mapping[alias] = rule
        return exprs, mapping


class _StreamingResultWriter:
    #: One JSON array per row: [{rule, field, message, value}, ...]
    DETAIL_COLUMN = "validation_errors"

    def __init__(self, logger: ILogger, result_mode: str = "errors_only"):
        self.logger = logger
        self.result_mode = result_mode
        self.temp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        self.file_path = self.temp.name
        self.temp.close()
        self.header_written = False
        self.has_content = False
        self.logger.debug(f"Created temporary result writer at: {self.file_path}")

    def write(self, df: pl.DataFrame, error_cols: List[str]):
        df_out = df

        struct_cols = [
            c for c in error_cols
            if c in df_out.columns and isinstance(df_out.schema.get(c), pl.Struct)
        ]

        if struct_cols:
            # Every rule reports {rule, field, message, value}. Flattening to the
            # message alone threw away which field failed and what it held, so a
            # caller could say "this row is wrong" but not point at the cell —
            # and the field name survived only inside an alias like `err_0_code`.
            #
            # `validation_errors` carries the whole struct, one JSON array per
            # row, which is what a frontend needs to render an error against a
            # field. The per-alias message columns stay exactly as they were, so
            # anything already reading them is unaffected.
            df_out = df_out.with_columns(
                pl.concat_list([
                    pl.when(pl.col(c).is_not_null())
                    .then(pl.col(c).struct.json_encode())
                    .otherwise(None)
                    for c in struct_cols
                ])
                .list.drop_nulls()
                .alias(self.DETAIL_COLUMN)
            )
            df_out = df_out.with_columns(
                pl.when(pl.col(self.DETAIL_COLUMN).list.len() > 0)
                .then(
                    pl.lit("[")
                    + pl.col(self.DETAIL_COLUMN).list.join(",")
                    + pl.lit("]")
                )
                .otherwise(pl.lit(""))
                .alias(self.DETAIL_COLUMN)
            )
            df_out = df_out.with_columns([
                pl.col(c).struct.field("message").alias(c)
                for c in struct_cols
            ])

        # Generate boolean column
        df_out = df_out.with_columns(
            pl.any_horizontal([pl.col(c).is_not_null() for c in error_cols]).alias("has_validation_error")
        )

        if self.result_mode == "errors_only":
            df_out = df_out.filter(pl.col("has_validation_error"))

        if df_out.is_empty():
            return
        
        # Append to CSV
        with open(self.file_path, "ab" if self.header_written else "wb") as f:
            df_out.write_csv(f, include_header=not self.header_written)
            
        self.header_written = True
        self.has_content = True

    def cleanup(self):
        if os.path.exists(self.file_path):
            self.logger.debug(f"Cleaning up temporary file: {self.file_path}")
            os.remove(self.file_path)
