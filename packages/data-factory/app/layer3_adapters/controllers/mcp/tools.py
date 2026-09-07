"""
MCP Tool definitions for the Data Factory service.

Exposes validation, transformation, and rule management capabilities
as MCP tools that LLM agents can discover and invoke.
"""

import sys
import os
import json
from typing import Optional, List, Dict, Any, Union
from dataclasses import asdict

# Add project root to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
# Also add libs/mcp/python to path for local dev
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", "..", "..", "libs", "mcp", "python"))

from simplemdg_mcp import create_mcp_server  # noqa: may raise ImportError, caught in main.py

mcp = create_mcp_server("data-factory")

# ──────────────────────────────────────────────
# Container accessor (set by main.py at startup)
# ──────────────────────────────────────────────
_container: Optional[dict] = None


def set_container(container: dict):
    """Called from main.py lifespan to inject the DI container."""
    global _container
    _container = container


def _get_usecase(name: str):
    if not _container:
        raise RuntimeError("MCP tools not initialized — container not set")
    uc = _container.get(name)
    if not uc:
        raise RuntimeError(f"UseCase '{name}' not available in container")
    return uc


def _to_list(value) -> list:
    """Accept both a JSON string or a native list from MCP arguments."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _to_dict(value) -> dict:
    """Accept both a JSON string or a native dict from MCP arguments."""
    if isinstance(value, str):
        return json.loads(value)
    return value


# ──────────────────────────────────────────────
# Validation Tools
# ──────────────────────────────────────────────

@mcp.tool()
async def validate_file(
    file_path: str,
    file_format: str = "csv",
    rules: Optional[List[Dict[str, Any]]] = None,
    rule_set_id: Optional[str] = None,
    version_id: Optional[str] = None,
    result_mode: str = "errors_only",
) -> str:
    """
    Validate a cloud-stored data file against validation rules.

    Provide EITHER inline rules OR a rule_set_id (not both).
    Returns validation summary including total rows, valid/invalid counts,
    and per-rule error details.

    Each inline rule should have: rule_name, type (required/unique/set_unique/expression),
    params (with columns and optional expression), and error_message.

    Args:
        file_path: Path or UUID of the file in cloud storage
        file_format: File format - csv, json, or xlsx
        rules: Array of inline validation rules (use this OR rule_set_id)
        rule_set_id: ID of a saved rule set to validate against (use this OR rules)
        version_id: Optional source version ID to validate a specific file version
        result_mode: Validation result output mode - errors_only or full_file
    """
    from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import (
        DataValidationCommand,
    )

    usecase = _get_usecase("data_validation_usecase")
    parsed_rules = _to_list(rules) if rules else None
    command = DataValidationCommand(
        file_id=file_path,
        version_id=version_id,
        file_format=file_format,
        rules=parsed_rules,
        rule_set_id=rule_set_id,
        result_mode=result_mode,
    )
    result = await usecase.execute(command)
    return json.dumps(asdict(result), default=str)


@mcp.tool()
async def query_validation_results(
    file_id: str,
    odata_query: str = "",
    version_id: Optional[str] = None,
) -> str:
    """
    Query validation result data with OData syntax.

    Supports $top, $skip, $filter, $select, $orderby parameters.
    Example odata_query: "$top=10&$filter=has_validation_error eq true"

    Args:
        file_id: File ID of the validation result file
        odata_query: OData query string for filtering/paging results
        version_id: Optional file version ID for querying a specific validation result version
    """
    usecase = _get_usecase("data_validation_usecase")
    result = usecase.query_result_file(file_id, odata_query, version_id=version_id)
    return json.dumps(result, default=str)


@mcp.tool()
async def get_validation_download_url(
    file_id: str,
    version_id: Optional[str] = None,
) -> str:
    """
    Generate a presigned download URL for a validation result file.

    Args:
        file_id: File ID of the result file to download
        version_id: Optional file version ID for a specific validation result version
    """
    usecase = _get_usecase("data_validation_usecase")
    url = usecase.get_download_url(file_id, version_id=version_id)
    return json.dumps({"file_url": url})


# ──────────────────────────────────────────────
# Transformation Tools
# ──────────────────────────────────────────────

@mcp.tool()
async def transform_file(
    file_id: str,
    rules: List[Dict[str, Any]],
    file_format: str = "csv",
    output_format: str = "csv",
    version_id: Optional[str] = None,
) -> str:
    """
    Apply transformation rules to a cloud-stored data file.

    Returns transformation result with download URL for the output file.

    Args:
        file_id: File ID of the file in cloud storage
        rules: Array of transformation rules
        file_format: Input file format - csv, json, or xlsx
        output_format: Output file format - csv, json, or xlsx
        version_id: Optional source version ID for optimistic concurrency
    """
    from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import (
        DataTransformationCommand,
    )

    usecase = _get_usecase("data_transformation_usecase")
    parsed_rules = _to_list(rules)
    command = DataTransformationCommand(
        file_id=file_id,
        file_format=file_format,
        output_format=output_format,
        rules=parsed_rules,
        version_id=version_id,
    )
    result = await usecase.execute(command)
    return json.dumps(asdict(result), default=str)


@mcp.tool()
async def transform_rows(
    file_path: str,
    operations: List[Dict[str, Any]],
    file_format: str = "csv",
    use_column_indices: bool = False,
) -> str:
    """
    Perform row-level transformations (insert/delete/update) on a cloud file.

    Supports three row identification methods:
    - index: by row position
    - condition: by expression evaluation
    - values: by exact value matching

    Each operation should have operation ("insert", "delete", or "update")
    and row_identifier (with method and value/values).
    Insert and update operations require data; insert also requires position.
    Update supports optional allow_multiple=true for bulk updates.

    Args:
        file_path: Path or UUID of the file in cloud storage
        operations: Array of row operations
        file_format: File format - csv, json, or xlsx
        use_column_indices: If true, use column indices instead of names
    """
    from app.layer2_application.features.data_transformation.use_cases.row_transformation_usecase import (
        RowTransformationCommand,
    )

    usecase = _get_usecase("row_transformation_usecase")
    parsed_ops = _to_list(operations)
    command = RowTransformationCommand(
        file_path=file_path,
        file_format=file_format,
        operations=parsed_ops,
        use_column_indices=use_column_indices,
    )
    result = await usecase.execute(command)
    return json.dumps(asdict(result), default=str)


# ──────────────────────────────────────────────
# Schema Transform Tools
# ──────────────────────────────────────────────

@mcp.tool()
async def inspect_file(
    file_path: str,
    file_format: str = "csv",
    sample_size: int = 5,
    sheet_names: Optional[List[str]] = None,
    merge_sheets: bool = False,
    add_sheet_name_column: bool = False,
) -> str:
    """
    Inspect a source file schema and return detected columns, dtypes,
    sample data, nested depth, and workbook metadata.

    Args:
        file_path: Path, UUID, URL, or local path to the source file
        file_format: Input file format - csv, json, or xlsx
        sample_size: Number of sample rows to include
        sheet_names: Optional Excel sheet names to read
        merge_sheets: If true, merge selected/all Excel sheets into one frame
        add_sheet_name_column: If true, add source_sheet column when merging sheets
    """
    from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
        InspectSchemaCommand,
    )

    usecase = _get_usecase("schema_transform_usecase")
    parsed_sheet_names = _to_list(sheet_names) if sheet_names else None
    command = InspectSchemaCommand(
        file_path=file_path,
        file_format=file_format,
        sample_size=sample_size,
        sheet_names=parsed_sheet_names,
        merge_sheets=merge_sheets,
        add_sheet_name_column=add_sheet_name_column,
    )
    result = await usecase.inspect_file(command)
    return json.dumps(asdict(result), default=str)


@mcp.tool()
async def generate_schema_mapping(
    source_schema: Dict[str, Any],
    target_schema: Dict[str, Any],
    source_type: str = "file",
    mapping_hints: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Generate source-to-target field mapping suggestions from schemas.

    Args:
        source_schema: Source schema payload, typically from inspect_file
        target_schema: Target schema or BDM-style mapping definition
        source_type: Logical source type label for reporting
        mapping_hints: Optional explicit source/target mapping hints
    """
    from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
        GenerateSchemaMappingCommand,
    )

    usecase = _get_usecase("schema_transform_usecase")
    parsed_source_schema = _to_dict(source_schema)
    parsed_target_schema = _to_dict(target_schema)
    parsed_mapping_hints = _to_list(mapping_hints) if mapping_hints else None
    command = GenerateSchemaMappingCommand(
        source_schema=parsed_source_schema,
        target_schema=parsed_target_schema,
        source_type=source_type,
        mapping_hints=parsed_mapping_hints,
    )
    result = await usecase.generate_mapping(command)
    return json.dumps(asdict(result), default=str)


@mcp.tool()
async def preview_schema_transform(
    sample_data: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
    file_format: str = "json",
    target_schema: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Preview a schema transformation in memory using sample data only.

    Args:
        sample_data: In-memory sample rows to transform
        rules: Array of schema transformation rules
        file_format: Sample data format, usually json
        target_schema: Optional target schema for comparison
    """
    from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
        PreviewSchemaTransformCommand,
    )

    usecase = _get_usecase("schema_transform_usecase")
    parsed_sample_data = _to_list(sample_data)
    parsed_rules = _to_list(rules)
    parsed_target_schema = _to_dict(target_schema) if target_schema else None
    command = PreviewSchemaTransformCommand(
        sample_data=parsed_sample_data,
        rules=parsed_rules,
        file_format=file_format,
        target_schema=parsed_target_schema,
    )
    result = await usecase.preview_transform(command)
    return json.dumps(asdict(result), default=str)


@mcp.tool()
async def execute_schema_transform(
    source_file_id: str,
    rules: List[Dict[str, Any]],
    file_format: str = "csv",
    output_format: str = "csv",
    target_schema: Optional[Dict[str, Any]] = None,
    field_mapping: Optional[List[Dict[str, Any]]] = None,
    source_version_id: Optional[str] = None,
    target_file_id: Optional[str] = None,
    target_version_id: Optional[str] = None,
    sheet_names: Optional[List[str]] = None,
    merge_sheets: bool = False,
    add_sheet_name_column: bool = False,
) -> str:
    """
    Execute a full schema transformation against a source file.

    Args:
        source_file_id: File ID of the source file in cloud storage
        rules: Array of schema transformation rules
        file_format: Input file format - csv, json, or xlsx
        output_format: Output file format - csv, json, or xlsx
        target_schema: Optional target schema for comparison
        field_mapping: Optional source-to-target field mapping list
        source_version_id: Optional source version ID for optimistic concurrency
        target_file_id: Optional existing target file ID to update in place
        target_version_id: Optional target version ID for optimistic concurrency
        sheet_names: Optional Excel sheet names to read
        merge_sheets: If true, merge selected/all Excel sheets before transform
        add_sheet_name_column: If true, add source_sheet column when merging sheets
    """
    from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
        ExecuteSchemaTransformCommand,
    )

    usecase = _get_usecase("schema_transform_usecase")
    parsed_rules = _to_list(rules)
    parsed_target_schema = _to_dict(target_schema) if target_schema else None
    parsed_field_mapping = _to_list(field_mapping) if field_mapping else None
    parsed_sheet_names = _to_list(sheet_names) if sheet_names else None
    command = ExecuteSchemaTransformCommand(
        source_file_id=source_file_id,
        rules=parsed_rules,
        file_format=file_format,
        output_format=output_format,
        target_schema=parsed_target_schema,
        field_mapping=parsed_field_mapping,
        source_version_id=source_version_id,
        target_file_id=target_file_id,
        target_version_id=target_version_id,
        sheet_names=parsed_sheet_names,
        merge_sheets=merge_sheets,
        add_sheet_name_column=add_sheet_name_column,
    )
    result = await usecase.execute(command)
    return json.dumps(asdict(result), default=str)


# ──────────────────────────────────────────────
# Rule Management Tools
# ──────────────────────────────────────────────

@mcp.tool()
async def list_rule_sets() -> str:
    """
    List all saved validation rule sets.

    Returns all rule sets with their IDs, names, rules, and status.
    Use this to discover available rule_set_ids for validate_file.
    """
    usecase = _get_usecase("rule_management_usecase")
    result = await usecase.get_all_rules()
    return json.dumps(asdict(result), default=str)


@mcp.tool()
async def create_rule_set(
    name: str,
    rules: List[Dict[str, Any]],
    description: str = "",
) -> str:
    """
    Create a new reusable validation rule set.

    Args:
        name: Name for the rule set
        rules: Array of validation rules
        description: Optional description of the rule set
    """
    from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import (
        CreateRuleSetCommand,
    )

    usecase = _get_usecase("rule_management_usecase")
    parsed_rules = _to_list(rules)
    command = CreateRuleSetCommand(name=name, rules=parsed_rules, description=description)
    result = await usecase.create_rule_set(command)
    return json.dumps(asdict(result), default=str)


@mcp.tool()
async def match_rules_to_headers(headers: List[str]) -> str:
    """
    Auto-suggest validation rules based on column headers.

    Given a list of column header names, returns matching rule templates
    and descriptions. Useful for automatically proposing rules when
    a user uploads a new file.

    Args:
        headers: Array of column header names, e.g. ["email", "age", "id"]
    """
    usecase = _get_usecase("rule_management_usecase")
    parsed_headers = _to_list(headers)
    rules, descs = await usecase.find_matching_rules(parsed_headers)
    return json.dumps({"rule_templates": rules, "descriptions": descs}, default=str)


# ──────────────────────────────────────────────
# Reference Data Tools
# ──────────────────────────────────────────────

@mcp.tool()
async def build_reference_keyset(
    source_file_path: str,
    source_format: str = "csv",
    key_columns: Optional[List[str]] = None,
    multi_value_columns: Optional[List[str]] = None,
    multi_value_delimiter: str = "|",
    hazard_filter_column: Optional[str] = None,
    hazard_keywords: Optional[List[str]] = None,
) -> str:
    """
    Materialize a reference key-set ONCE from a large source file (e.g. the PubChem
    GHS compound CSV) into a compact, de-duplicated parquet of normalized lookup keys.

    Explodes delimited multi-value columns (e.g. Synonyms), optionally keeps only rows
    whose hazard column matches given keywords, normalizes (lower + trim), de-duplicates,
    and writes parquet. Returns the parquet URL + key_count — register that URL in
    governance.reference_data (purpose=dangerously_validate), then semi-join against it
    via validate_file's `reference_lookup` rule (O(n + m), not O(n * m)).

    Args:
        source_file_path: Path/UUID/URL of the source file in cloud storage
        source_format: Source format (default csv)
        key_columns: Single-value columns to use as keys (e.g. ["Name", "cid"])
        multi_value_columns: Delimited columns to explode into keys (e.g. ["Synonyms"])
        multi_value_delimiter: Delimiter inside multi_value_columns (default "|")
        hazard_filter_column: Optional column to keep only "dangerous" rows by (e.g. "Annotation_Content")
        hazard_keywords: Optional keywords; keep rows whose hazard column contains any of them
    """
    from app.layer2_application.features.reference_data.use_cases.build_reference_keyset_usecase import (
        BuildReferenceKeysetCommand,
    )

    usecase = _get_usecase("build_reference_keyset_usecase")
    command = BuildReferenceKeysetCommand(
        source_file_path=source_file_path,
        source_format=source_format,
        key_columns=_to_list(key_columns) if key_columns else [],
        multi_value_columns=_to_list(multi_value_columns) if multi_value_columns else [],
        multi_value_delimiter=multi_value_delimiter,
        hazard_filter_column=hazard_filter_column,
        hazard_keywords=_to_list(hazard_keywords) if hazard_keywords else None,
    )
    result = await usecase.execute(command)
    return json.dumps(asdict(result), default=str)
