from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.storage_interface import FileVersionConflictError
from app.layer2_application.interfaces.schema_transformer_interface import (
    ISchemaTransformerProvider,
)
from app.layer1_domain.entities.schema_transform import (
    FileSchema,
    SchemaTransformPreview,
    SchemaTransformExecutionResult,
    SchemaMappingResult,
)


@dataclass(kw_only=True)
class InspectSchemaCommand:
    file_path: str
    file_format: str = "csv"
    sample_size: int = 5
    version_id: Optional[str] = None
    sheet_names: Optional[List[str]] = None
    merge_sheets: bool = False
    add_sheet_name_column: bool = False
    header_row: Optional[int] = None


@dataclass(kw_only=True)
class GenerateSchemaMappingCommand:
    source_schema: Dict[str, Any]
    target_schema: Dict[str, Any]
    source_type: str = "file"
    mapping_hints: Optional[List[Dict[str, Any]]] = None


@dataclass(kw_only=True)
class PreviewSchemaTransformCommand:
    sample_data: List[Dict[str, Any]]
    rules: List[Dict[str, Any]]
    file_format: str = "json"
    target_schema: Optional[Dict[str, Any]] = None


@dataclass(kw_only=True)
class ExecuteSchemaTransformCommand:
    source_file_id: str
    rules: List[Dict[str, Any]]
    file_format: str = "csv"
    output_format: str = "csv"
    target_schema: Optional[Dict[str, Any]] = None
    field_mapping: Optional[List[Dict[str, Any]]] = None
    source_version_id: Optional[str] = None
    target_file_id: Optional[str] = None
    target_version_id: Optional[str] = None
    sheet_names: Optional[List[str]] = None
    merge_sheets: bool = False
    add_sheet_name_column: bool = False
    header_row: Optional[int] = None
    #: Ask the reader for the workbook banner so each column gets its
    #: ``SECTION.field`` path. Off by default -- it costs an extra small read.
    derive_field_paths: bool = False
    field_path_overrides: Optional[Dict[str, str]] = None


@dataclass(kw_only=True)
class SchemaInspectionResult:
    success: bool
    result: Optional[FileSchema] = None
    message: str = ""


@dataclass(kw_only=True)
class SchemaMappingGenerationResult:
    success: bool
    result: Optional[SchemaMappingResult] = None
    message: str = ""


@dataclass(kw_only=True)
class SchemaTransformPreviewResult:
    success: bool
    result: Optional[SchemaTransformPreview] = None
    message: str = ""


@dataclass(kw_only=True)
class SchemaTransformResult:
    success: bool
    result: Optional[SchemaTransformExecutionResult] = None
    message: str = ""


class SchemaTransformUseCase:
    def __init__(
        self,
        logger: ILogger,
        transformer: ISchemaTransformerProvider,
    ):
        self.logger = logger
        self.transformer = transformer

    async def inspect_file(self, command: InspectSchemaCommand) -> SchemaInspectionResult:
        self.logger.info(f"Inspecting schema for file {command.file_path}")

        try:
            result = self.transformer.inspect_schema(
                command.file_path,
                command.file_format,
                command.sample_size,
                command.version_id,
                command.sheet_names,
                command.merge_sheets,
                command.add_sheet_name_column,
                command.header_row,
            )
            return SchemaInspectionResult(
                success=True,
                result=result,
                message="Schema inspected successfully",
            )
        except Exception as e:
            self.logger.error(f"Schema inspection error: {e}")
            return SchemaInspectionResult(success=False, message=str(e))

    async def generate_mapping(
        self, command: GenerateSchemaMappingCommand
    ) -> SchemaMappingGenerationResult:
        self.logger.info("Generating schema mapping between source and target schema")

        try:
            result = self.transformer.generate_mapping(
                command.source_schema,
                command.target_schema,
                command.source_type,
                command.mapping_hints,
            )
            return SchemaMappingGenerationResult(
                success=True,
                result=result,
                message=result.message or "Schema mapping generated successfully",
            )
        except Exception as e:
            self.logger.error(f"Schema mapping generation error: {e}")
            return SchemaMappingGenerationResult(success=False, message=str(e))

    async def preview_transform(
        self, command: PreviewSchemaTransformCommand
    ) -> SchemaTransformPreviewResult:
        self.logger.info("Previewing schema transformation on sample data")

        try:
            result = self.transformer.preview_schema_transform(
                command.sample_data,
                command.rules,
                command.file_format,
                command.target_schema,
            )
            return SchemaTransformPreviewResult(
                success=True,
                result=result,
                message="Schema transform preview generated successfully",
            )
        except Exception as e:
            self.logger.error(f"Schema transform preview error: {e}")
            return SchemaTransformPreviewResult(success=False, message=str(e))

    async def execute(
        self, command: ExecuteSchemaTransformCommand
    ) -> SchemaTransformResult:
        self.logger.info(
            f"Executing schema transformation for file {command.source_file_id}"
        )

        try:
            result = await self.transformer.execute_schema_transform(
                command.source_file_id,
                command.file_format,
                command.output_format,
                command.rules,
                command.target_schema,
                command.field_mapping,
                command.source_version_id,
                command.target_file_id,
                command.target_version_id,
                command.sheet_names,
                command.merge_sheets,
                command.add_sheet_name_column,
                command.header_row,
                command.derive_field_paths,
                command.field_path_overrides,
            )
            return SchemaTransformResult(
                success=result.success,
                result=result,
                message=result.message,
            )
        except FileVersionConflictError as e:
            self.logger.warning(f"Schema transformation version conflict: {e}")
            return SchemaTransformResult(
                success=False,
                result=SchemaTransformExecutionResult(
                    success=False,
                    total_rows=0,
                    total_columns=0,
                    output_schema=FileSchema(),
                    message=str(e),
                    source_file_id=command.source_file_id,
                    source_version_id=command.source_version_id,
                    current_version_id=e.current_version_id,
                    stale_version=True,
                ),
                message=str(e),
            )
        except Exception as e:
            self.logger.error(f"Schema transformation error: {e}")
            return SchemaTransformResult(success=False, message=str(e))
