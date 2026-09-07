from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass(kw_only=True)
class SchemaField:
    name: str
    dtype: str
    fields: List["SchemaField"] = field(default_factory=list)


@dataclass(kw_only=True)
class FileSchema:
    columns: List[SchemaField] = field(default_factory=list)
    sample_data: List[Dict[str, Any]] = field(default_factory=list)
    nested_depth: int = 1
    file_format: str = "csv"
    total_columns: int = 0
    workbook_sheets: List[str] = field(default_factory=list)
    selected_sheets: List[str] = field(default_factory=list)
    merged_sheets: bool = False


@dataclass(kw_only=True)
class FieldMappingSuggestion:
    source_field: str
    target_field: str
    confidence: float
    reason: str = ""
    source_dtype: str = ""
    target_description: str = ""


@dataclass(kw_only=True)
class SchemaMappingResult:
    mappings: List[FieldMappingSuggestion] = field(default_factory=list)
    unmapped_source_fields: List[str] = field(default_factory=list)
    unmapped_target_fields: List[str] = field(default_factory=list)
    suggested_rules: List[Dict[str, Any]] = field(default_factory=list)
    target_schema_columns: List[str] = field(default_factory=list)
    message: str = ""


@dataclass(kw_only=True)
class SchemaTransformPreview:
    preview_data: List[Dict[str, Any]] = field(default_factory=list)
    output_schema: FileSchema = field(default_factory=FileSchema)
    matches_target_schema: Optional[bool] = None
    missing_columns: List[str] = field(default_factory=list)
    unexpected_columns: List[str] = field(default_factory=list)


@dataclass(kw_only=True)
class SchemaTransformExecutionResult:
    success: bool
    total_rows: int
    total_columns: int
    output_schema: FileSchema
    preview_data: List[Dict[str, Any]] = field(default_factory=list)
    download_url: str = ""
    output_file_id: str = ""
    output_version_id: str = ""
    source_file_id: str = ""
    source_version_id: Optional[str] = None
    current_version_id: Optional[str] = None
    stale_version: bool = False
    applied_rules: List[Dict[str, Any]] = field(default_factory=list)
    #: Column label -> ``SECTION.field``, when the caller asked for it. Empty
    #: otherwise, so nothing that ignores paths sees a change.
    field_paths: Dict[str, str] = field(default_factory=dict)
    message: str = ""
