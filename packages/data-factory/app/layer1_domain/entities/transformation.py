from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class RowIdentifier:
    type: str  # "index", "condition", "values"
    index: Optional[int] = None
    expression: Optional[str] = None
    match_values: Optional[Dict[str, Any]] = None


@dataclass
class RowOperation:
    operation: str  # "insert", "delete", "update"
    row_identifier: RowIdentifier
    data: Optional[Dict[str, Any]] = None  # for insert/update operations
    position: Optional[str] = None  # "before", "after", "at_index" for insert
    use_column_indices: bool = False  # whether data keys are column indices
    allow_multiple: bool = False  # for update operations


@dataclass
class RowTransformResult:
    success: bool
    affected_rows: int
    total_rows: int
    message: str
    preview_data: Optional[List[Dict[str, Any]]] = None
    download_url: Optional[str] = None


@dataclass(kw_only=True)
class TransformRule:
    rule_name: str
    type: str
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(kw_only=True)
class TransformResult:
    success: bool
    total_rows: int
    total_columns: int
    odata: Any
    message: str
    download_url: str = ""
    output_file_id: str = ""
    output_version_id: str = ""
    source_file_id: str = ""
    source_version_id: Optional[str] = None
    current_version_id: Optional[str] = None
    stale_version: bool = False
