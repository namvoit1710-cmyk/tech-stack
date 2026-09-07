from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.transformer_interface import ITransformerProvider
from app.layer2_application.interfaces.storage_interface import (
    IFileStorageProvider,
    FileVersionConflictError,
)
from app.layer2_application.interfaces.odata_parser_interface import IODataParser
from app.layer1_domain.entities.transformation import TransformRule, TransformResult


@dataclass(kw_only=True)
class DataTransformationCommand:
    file_id: str
    rules: List[Dict[str, Any]]
    file_format: str = "csv"
    output_format: str = "csv"
    version_id: Optional[str] = None


@dataclass(kw_only=True)
class DataTransformationResult:
    success: bool
    result: Optional[TransformResult] = None
    message: str = ""


class DataTransformationUseCase:
    def __init__(
        self,
        logger: ILogger,
        transformer: ITransformerProvider,
        storage: IFileStorageProvider,
        odata_parser: IODataParser
    ):
        self.logger = logger
        self.transformer = transformer
        self.storage = storage
        self.odata_parser = odata_parser

    async def execute(self, command: DataTransformationCommand) -> DataTransformationResult:
        self.logger.info(f"Executing DataTransformationUseCase for file {command.file_id}")

        domain_rules = [TransformRule(
            rule_name=r.get("rule_name", ""),
            type=r.get("type", "filter"),
            params=r.get("params", {}),
            description=r.get("description", "")
        ) for r in command.rules]

        try:
            trans_result = await self.transformer.transform_cloud_file(
                command.file_id,
                command.file_format,
                command.output_format,
                domain_rules,
                command.version_id,
            )
            return DataTransformationResult(
                success=trans_result.success,
                result=trans_result,
                message=trans_result.message
            )
        except FileVersionConflictError as e:
            self.logger.warning(f"Transformation version conflict: {e}")
            return DataTransformationResult(
                success=False,
                result=TransformResult(
                    success=False,
                    total_rows=0,
                    total_columns=0,
                    odata=None,
                    message=str(e),
                    source_file_id=command.file_id,
                    source_version_id=command.version_id,
                    current_version_id=e.current_version_id,
                    stale_version=True,
                ),
                message=str(e),
            )
        except Exception as e:
            self.logger.error(f"Transformation error: {e}")
            return DataTransformationResult(success=False, message=str(e))

    def get_download_url(self, file_id: str, version_id: Optional[str] = None) -> str:
        return self.storage.generate_presigned_url(file_id, "download", version_id=version_id)

    def query_result_file(
        self,
        file_id: str,
        odata_query: str,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ext = file_id.split('.')[-1].lower()
        if ext not in ['csv', 'json', 'xlsx']:
            ext = 'csv'
        df = self.storage.download_and_read(file_id, ext, version_id=version_id)
        return self.odata_parser.parse_and_format(df, odata_query)
