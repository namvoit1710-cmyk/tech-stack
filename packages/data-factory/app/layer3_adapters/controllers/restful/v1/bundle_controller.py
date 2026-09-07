from typing import Any, Dict, List, Optional

from dataclasses import asdict
from fastapi import APIRouter, Body, HTTPException, Request

from pydantic import field_validator, model_validator

from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto
from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_schema_transform_rules,
    validate_validation_rules,
)
from app.layer2_application.features.bundle.use_cases.transform_validate_bundle_usecase import (
    BundleTableSpec,
    TransformValidateBundleCommand,
)


router = APIRouter()


class BundleTableInput(BaseInputDto):
    """One logical table cut out of the shared source file."""

    name: str
    sheet_names: Optional[List[str]] = None
    header_row: Optional[int] = None
    rules: List[Dict[str, Any]] = []
    validation_rules: Optional[List[Dict[str, Any]]] = None
    validation_rule_set_id: Optional[str] = None
    result_mode: str = "errors_only"
    depends_on: List[str] = []
    #: Column label -> path, for this table only, merged over the bundle-wide
    #: map. A label is not unique across a workbook -- Supplier (String) is
    #: PURCHASING.supplier on one sheet and SALESPRICE.supplier on another.
    field_path_overrides: Optional[Dict[str, str]] = None

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value):
        if not value or not value.strip():
            raise ValueError("table name must not be blank")
        return value.strip()

    @field_validator("header_row")
    @classmethod
    def _header_row_is_a_row_index(cls, value):
        if value is not None and value < 0:
            raise ValueError("header_row must be a 0-based row index >= 0")
        return value

    @field_validator("result_mode")
    @classmethod
    def _result_mode_is_known(cls, value):
        allowed = {"errors_only", "error_rows", "errors", "full_file", "full_rows",
                   "all_rows", "full"}
        if value and value.strip().lower() not in allowed:
            raise ValueError(
                f"result_mode must be one of {', '.join(sorted(allowed))}; got '{value}'"
            )
        return value

    @field_validator("rules")
    @classmethod
    def _shape_rules_are_executable(cls, value):
        try:
            return validate_schema_transform_rules(value)
        except RuleSpecError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("validation_rules")
    @classmethod
    def _validation_rules_are_executable(cls, value):
        try:
            return validate_validation_rules(value, label="validation_rules")
        except RuleSpecError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _validation_is_configured_one_way(self):
        if self.validation_rules and self.validation_rule_set_id:
            raise ValueError(
                f"table '{self.name}': give either validation_rules or "
                "validation_rule_set_id, not both"
            )
        return self


class TransformValidateBundleInput(BaseInputDto):
    source_file_id: Optional[str] = None
    file_path: Optional[str] = None
    tables: List[BundleTableInput]
    file_format: str = "xlsx"
    output_format: str = "csv"
    source_version_id: Optional[str] = None
    default_header_row: Optional[int] = None
    fail_fast: bool = False
    #: Read the workbook banner so violations carry SECTION.field as well as the
    #: column label. On by default: a bundle is reading a mass-upload template.
    derive_field_paths: bool = True
    #: Column label -> the path the template is configured with. Wins over the
    #: name derived from the label, which is a camel-case guess.
    field_path_overrides: Optional[Dict[str, str]] = None

    @field_validator("default_header_row")
    @classmethod
    def _header_row_is_a_row_index(cls, value):
        if value is not None and value < 0:
            raise ValueError("default_header_row must be a 0-based row index >= 0")
        return value

    @model_validator(mode="after")
    def _table_graph_is_coherent(self):
        names = [t.name for t in self.tables]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            # Two tables under one name silently overwrite each other's file id,
            # so a later join_reference would resolve to the wrong table.
            raise ValueError(f"table names must be unique; repeated: {duplicates}")

        produced = set()
        for table in self.tables:
            unmet = [n for n in table.depends_on if n not in produced]
            if unmet:
                raise ValueError(
                    f"table '{table.name}' depends on {unmet}, which is not listed "
                    "before it; order 'tables' so dependencies come first"
                )
            for rule in table.rules or []:
                referenced = (rule.get("params") or {}).get("table")
                if referenced and referenced not in produced:
                    raise ValueError(
                        f"table '{table.name}' has a rule referencing table "
                        f"'{referenced}', which is not produced before it"
                    )
            produced.add(table.name)
        return self


def get_usecase(request: Request):
    return request.app.state.container.get("transform_validate_bundle_usecase")


@router.post("/transform-validate")
async def transform_validate_bundle(
    request: Request, dto: TransformValidateBundleInput = Body(...)
):
    """Cut a multi-table source file into its logical tables, then validate each.

    Tables run in the order given. Each table's output file_id is registered
    under its name, so a later table can reach an earlier one from a rule by
    naming it (``params.table``) instead of a file_id the caller cannot know yet.
    """
    usecase = get_usecase(request)
    if not usecase:
        raise HTTPException(500, "UseCase not wired")

    source_file_id = dto.source_file_id or dto.file_path
    if not source_file_id:
        raise HTTPException(400, "source_file_id is required")
    if not dto.tables:
        raise HTTPException(400, "At least one table is required")

    command = TransformValidateBundleCommand(
        source_file_id=source_file_id,
        tables=[
            BundleTableSpec(
                name=table.name,
                sheet_names=table.sheet_names,
                header_row=table.header_row,
                rules=table.rules,
                field_path_overrides=table.field_path_overrides,
                validation_rules=table.validation_rules,
                validation_rule_set_id=table.validation_rule_set_id,
                result_mode=table.result_mode,
                depends_on=table.depends_on,
            )
            for table in dto.tables
        ],
        file_format=dto.file_format,
        output_format=dto.output_format,
        source_version_id=dto.source_version_id,
        default_header_row=dto.default_header_row,
        fail_fast=dto.fail_fast,
        derive_field_paths=dto.derive_field_paths,
        field_path_overrides=dto.field_path_overrides,
    )

    result = await usecase.execute(command)
    payload = asdict(result)
    if not result.success and not result.tables:
        # Nothing ran at all: that is a bad request, not a partial result.
        raise HTTPException(400, result.message)
    return payload
