"""Run one multi-table source file through transform-then-validate, table by table.

A mass-upload workbook is not one table; it is a bundle of logical tables that
share a business key. An SMDG ART41.00 template carries 29 of them across 23
sheets, several sheets holding a second table side-by-side in later columns.
Driving that from the caller means 58 ordered calls plus bookkeeping of which
output file feeds which later rule, which is orchestration nobody should have to
re-implement per client.

This use case owns that loop. It composes the two existing use cases rather than
reimplementing either: each table is transformed by ``SchemaTransformUseCase``
and then validated by ``DataValidationUseCase``. Tables run in listed order, and
every table's output ``file_id`` is registered under its name, so a later table
can reach an earlier one by name through a ``join_reference`` rule -- which is
what a cross-table derivation needs.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import (
    DataValidationCommand,
)
from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
    ExecuteSchemaTransformCommand,
)


@dataclass(kw_only=True)
class BundleTableSpec:
    """One logical table cut out of the shared source file."""

    name: str
    sheet_names: Optional[List[str]] = None
    header_row: Optional[int] = None
    rules: List[Dict[str, Any]] = field(default_factory=list)
    #: Validation rules for this table, or a stored rule-set id.
    validation_rules: Optional[List[Dict[str, Any]]] = None
    validation_rule_set_id: Optional[str] = None
    result_mode: str = "errors_only"
    #: Column label -> path, for this table only. A label is not unique across
    #: a workbook: ``Supplier (String)`` is PURCHASING.supplier on one sheet and
    #: SALESPRICE.supplier on another, so a single bundle-wide map cannot express
    #: both. These win over the bundle-wide ones.
    field_path_overrides: Optional[Dict[str, str]] = None
    #: Table names that must be materialized before this one, because a rule
    #: here reaches into them. Order in ``tables`` already implies this; naming
    #: it makes the dependency explicit and lets us fail with a clear message.
    depends_on: List[str] = field(default_factory=list)


@dataclass(kw_only=True)
class TransformValidateBundleCommand:
    source_file_id: str
    tables: List[BundleTableSpec]
    file_format: str = "xlsx"
    output_format: str = "csv"
    source_version_id: Optional[str] = None
    #: Applied to any table that does not pin its own.
    default_header_row: Optional[int] = None
    #: Stop the whole bundle at the first table that fails, instead of
    #: continuing and reporting per-table outcomes.
    fail_fast: bool = False
    #: Read the workbook banner so violations name ``SECTION.field`` as well as
    #: the column label. On by default here: a bundle is reading a mass-upload
    #: template, which is exactly the shape that has a banner to read.
    derive_field_paths: bool = True
    field_path_overrides: Optional[Dict[str, str]] = None


@dataclass(kw_only=True)
class BundleTableResult:
    name: str
    success: bool
    message: str = ""
    output_file_id: str = ""
    output_version_id: str = ""
    total_rows: int = 0
    total_columns: int = 0
    validation: Optional[Dict[str, Any]] = None


@dataclass(kw_only=True)
class TransformValidateBundleResult:
    success: bool
    source_file_id: str
    tables: List[BundleTableResult] = field(default_factory=list)
    table_file_ids: Dict[str, str] = field(default_factory=dict)
    total_rows: int = 0
    invalid_rows: int = 0
    message: str = ""


class TransformValidateBundleUseCase:
    def __init__(
        self,
        logger: ILogger,
        schema_transform_usecase: Any,
        data_validation_usecase: Any = None,
    ):
        self.logger = logger
        self.schema_transform_usecase = schema_transform_usecase
        self.data_validation_usecase = data_validation_usecase

    async def execute(
        self, command: TransformValidateBundleCommand
    ) -> TransformValidateBundleResult:
        if not command.tables:
            return TransformValidateBundleResult(
                success=False,
                source_file_id=command.source_file_id,
                message="No tables were given for this bundle.",
            )

        duplicates = self._duplicate_names(command.tables)
        if duplicates:
            return TransformValidateBundleResult(
                success=False,
                source_file_id=command.source_file_id,
                message=(
                    f"Table names must be unique within a bundle; repeated: {duplicates}."
                ),
            )

        self.logger.info(
            f"Running bundle over {command.source_file_id}: {len(command.tables)} tables."
        )

        results: List[BundleTableResult] = []
        produced: Dict[str, str] = {}
        produced_versions: Dict[str, str] = {}
        total_rows = 0
        invalid_rows = 0

        for spec in command.tables:
            unmet = [name for name in spec.depends_on if name not in produced]
            if unmet:
                message = (
                    f"Table '{spec.name}' depends on {unmet}, which "
                    "has not been produced yet. List dependencies earlier in 'tables'."
                )
                self.logger.warning(message)
                results.append(
                    BundleTableResult(name=spec.name, success=False, message=message)
                )
                if command.fail_fast:
                    break
                continue

            table_result = await self._run_table(
                command, spec, produced, produced_versions
            )
            results.append(table_result)

            if table_result.success:
                produced[spec.name] = table_result.output_file_id
                produced_versions[spec.name] = table_result.output_version_id
                total_rows += table_result.total_rows
                invalid_rows += self._invalid_rows(table_result.validation)
            elif command.fail_fast:
                break

        failed = [result.name for result in results if not result.success]
        skipped = len(command.tables) - len(results)
        message = f"{len(results) - len(failed)}/{len(command.tables)} tables completed."
        if failed:
            message += f" Failed: {failed}."
        if skipped:
            # Never let a short-circuit read as full coverage.
            message += f" {skipped} table(s) not attempted (fail_fast)."

        return TransformValidateBundleResult(
            success=not failed and not skipped,
            source_file_id=command.source_file_id,
            tables=results,
            table_file_ids=produced,
            total_rows=total_rows,
            invalid_rows=invalid_rows,
            message=message,
        )

    async def _run_table(
        self,
        command: TransformValidateBundleCommand,
        spec: BundleTableSpec,
        produced: Dict[str, str],
        produced_versions: Dict[str, str],
    ) -> BundleTableResult:
        header_row = spec.header_row if spec.header_row is not None else command.default_header_row
        rules = self._resolve_table_references(spec.rules, produced, produced_versions)

        transform = await self.schema_transform_usecase.execute(
            ExecuteSchemaTransformCommand(
                source_file_id=command.source_file_id,
                rules=rules,
                file_format=command.file_format,
                output_format=command.output_format,
                source_version_id=command.source_version_id,
                sheet_names=spec.sheet_names,
                header_row=header_row,
                derive_field_paths=command.derive_field_paths,
                field_path_overrides={**(command.field_path_overrides or {}),
                                      **(spec.field_path_overrides or {})} or None,
            )
        )

        if not transform.success or not transform.result:
            return BundleTableResult(
                name=spec.name,
                success=False,
                message=f"Transform failed: {transform.message}",
            )

        result = BundleTableResult(
            name=spec.name,
            success=True,
            message=transform.message,
            output_file_id=transform.result.output_file_id,
            output_version_id=transform.result.output_version_id,
            total_rows=transform.result.total_rows,
            total_columns=transform.result.total_columns,
        )

        if not (spec.validation_rules or spec.validation_rule_set_id):
            return result

        if not self.data_validation_usecase:
            result.message = (
                f"{result.message} Validation skipped: the validation use case is not wired."
            )
            return result

        validation = await self.data_validation_usecase.execute(
            DataValidationCommand(
                file_id=result.output_file_id,
                version_id=result.output_version_id or None,
                file_format=command.output_format,
                rules=spec.validation_rules,
                rule_set_id=spec.validation_rule_set_id,
                result_mode=spec.result_mode,
                field_paths=getattr(transform.result, "field_paths", None) or None,
            )
        )

        if not validation.success:
            result.success = False
            result.message = f"Validation failed: {validation.message}"
            return result

        validation_result = validation.result
        result.validation = {
            "total_rows": getattr(validation_result, "total_rows", 0),
            "valid_rows": getattr(validation_result, "valid_rows", 0),
            "invalid_rows": getattr(validation_result, "invalid_rows", 0),
            "result_file_id": getattr(
                getattr(validation_result, "odata", None), "result_file_id", ""
            ),
            "result_file_url": getattr(
                getattr(validation_result, "odata", None), "result_file_url", ""
            ),
            "message": validation.message,
        }
        return result

    def _resolve_table_references(
        self,
        rules: List[Dict[str, Any]],
        produced: Dict[str, str],
        produced_versions: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Turn ``{"table": "ArticleMaster"}`` into the file_id it produced.

        Rules are authored against table names because the caller cannot know the
        file ids the run will mint. Only rules that name a table are rewritten;
        everything else passes through untouched.
        """
        resolved: List[Dict[str, Any]] = []
        for rule in rules or []:
            params = rule.get("params") if isinstance(rule, dict) else None
            table = (params or {}).get("table")
            if not table:
                resolved.append(rule)
                continue

            if table not in produced:
                self.logger.warning(
                    f"Rule references table '{table}', which has not been produced. "
                    "Leaving the rule as-is; it will be skipped downstream."
                )
                resolved.append(rule)
                continue

            new_params = {key: value for key, value in params.items() if key != "table"}
            new_params.setdefault("file_id", produced[table])
            # The reference is a produced CSV, whatever the bundle source was.
            new_params.setdefault("file_format", "csv")
            if produced_versions.get(table):
                new_params.setdefault("version_id", produced_versions[table])
            resolved.append({**rule, "params": new_params})
        return resolved

    @staticmethod
    def _duplicate_names(tables: List[BundleTableSpec]) -> List[str]:
        seen, duplicates = set(), []
        for table in tables:
            if table.name in seen and table.name not in duplicates:
                duplicates.append(table.name)
            seen.add(table.name)
        return duplicates

    @staticmethod
    def _invalid_rows(validation: Optional[Dict[str, Any]]) -> int:
        if not validation:
            return 0
        return int(validation.get("invalid_rows") or 0)
