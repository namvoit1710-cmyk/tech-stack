from typing import Protocol, List, Dict, Any, Optional

from app.layer1_domain.entities.schema_transform import (
    FileSchema,
    SchemaTransformPreview,
    SchemaTransformExecutionResult,
    SchemaMappingResult,
)


class ISchemaTransformerProvider(Protocol):
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
    ) -> FileSchema: ...

    def generate_mapping(
        self,
        source_schema: Dict[str, Any],
        target_schema: Dict[str, Any],
        source_type: str = "file",
        mapping_hints: Optional[List[Dict[str, Any]]] = None,
    ) -> SchemaMappingResult: ...

    def preview_schema_transform(
        self,
        sample_data: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        file_format: str = "json",
        target_schema: Optional[Dict[str, Any]] = None,
    ) -> SchemaTransformPreview: ...

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
    ) -> SchemaTransformExecutionResult: ...
