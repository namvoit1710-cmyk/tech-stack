from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.storage_interface import IFileStorageProvider
from app.layer2_application.interfaces.transformer_interface import IRowTransformerProvider
from app.layer1_domain.entities.transformation import (
    RowOperation, RowIdentifier, RowTransformResult
)


@dataclass(kw_only=True)
class RowTransformationCommand:
    file_path: str
    operations: List[Dict[str, Any]]
    file_format: str = "csv"
    use_column_indices: bool = False
    version_id: Optional[str] = None


@dataclass(kw_only=True)
class RowTransformationResult:
    success: bool
    result: Optional[RowTransformResult] = None
    message: str = ""


class RowTransformationUseCase:
    def __init__(
        self,
        logger: ILogger,
        storage: IFileStorageProvider,
        row_transformer: IRowTransformerProvider
    ):
        self.logger = logger
        self.storage = storage
        self.row_transformer = row_transformer

    async def execute(self, command: RowTransformationCommand) -> RowTransformationResult:
        self.logger.info(f"Executing RowTransformationUseCase for file {command.file_path}")

        try:
            # Convert operations to RowOperation objects with validation
            operations = []
            for op_data in command.operations:
                # Validate operation type
                operation_type = op_data.get("operation")
                if not operation_type or operation_type not in ["insert", "delete", "update"]:
                    raise ValueError(
                        f"Invalid operation type: {operation_type}. "
                        "Must be 'insert', 'delete', or 'update'"
                    )

                # Validate and build row identifier
                row_identifier_data = op_data.get("row_identifier", {})
                if not row_identifier_data:
                    raise ValueError("row_identifier is required for all operations")

                identifier_type = row_identifier_data.get("type")
                if not identifier_type:
                    raise ValueError("row_identifier.type is required")

                if identifier_type not in ["index", "condition", "values"]:
                    raise ValueError(f"Invalid identifier_type: {identifier_type}. Must be 'index', 'condition', or 'values'")

                # Validate type-specific requirements
                if identifier_type == "index":
                    index_value = row_identifier_data.get("index")
                    if index_value is None:
                        raise ValueError("row_identifier.index is required when type is 'index'")
                    try:
                        index_value = int(index_value)
                    except (ValueError, TypeError):
                        raise ValueError("row_identifier.index must be a valid integer")

                elif identifier_type == "condition":
                    expression_value = row_identifier_data.get("expression")
                    if not expression_value or not isinstance(expression_value, str):
                        raise ValueError("row_identifier.expression is required and must be a string when type is 'condition'")

                elif identifier_type == "values":
                    match_values = row_identifier_data.get("match_values")
                    if not match_values or not isinstance(match_values, dict):
                        raise ValueError("row_identifier.match_values is required and must be a dictionary when type is 'values'")

                # Validate insert-specific fields
                if operation_type == "insert":
                    data = op_data.get("data")
                    if not data or not isinstance(data, dict):
                        raise ValueError("data is required and must be a dictionary for insert operations")

                    position = op_data.get("position")
                    if not position or position not in ["before", "after", "at_index"]:
                        raise ValueError(f"position is required for insert operations and must be 'before', 'after', or 'at_index'. Got: {position}")

                if operation_type == "update":
                    data = op_data.get("data")
                    if not data or not isinstance(data, dict):
                        raise ValueError("data is required and must be a dictionary for update operations")

                    if op_data.get("position") is not None:
                        raise ValueError("position is not supported for update operations")

                    allow_multiple = op_data.get("allow_multiple", False)
                    if not isinstance(allow_multiple, bool):
                        raise ValueError("allow_multiple must be a boolean for update operations")

                row_identifier = RowIdentifier(
                    type=identifier_type,
                    index=row_identifier_data.get("index"),
                    expression=row_identifier_data.get("expression"),
                    match_values=row_identifier_data.get("match_values")
                )

                operation = RowOperation(
                    operation=operation_type,
                    row_identifier=row_identifier,
                    data=op_data.get("data"),
                    position=op_data.get("position"),
                    use_column_indices=op_data.get("use_column_indices", False),
                    allow_multiple=op_data.get("allow_multiple", False),
                )
                operations.append(operation)

            # Execute row transformations
            transform_result = await self.row_transformer.transform_rows(
                file_path=command.file_path,
                file_format=command.file_format,
                operations=operations,
                use_column_indices=command.use_column_indices,
                version_id=command.version_id,
            )

            return RowTransformationResult(
                success=transform_result.success,
                result=transform_result,
                message=transform_result.message
            )

        except Exception as e:
            self.logger.error(f"Row transformation error: {e}")
            return RowTransformationResult(success=False, message=str(e))

    def get_download_url(self, file_name: str) -> str:
        return self.storage.generate_presigned_url(file_name, "download")