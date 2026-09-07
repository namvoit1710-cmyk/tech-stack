from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.validator_interface import IValidatorProvider
from app.layer2_application.repositories.rule_repository_interface import IRuleRepository
from app.layer2_application.interfaces.storage_interface import IFileStorageProvider
from app.layer2_application.interfaces.odata_parser_interface import IODataParser
from app.layer1_domain.entities.validation import ValidationRule, ValidationResult


@dataclass(kw_only=True)
class DataValidationCommand:
    file_id: str
    version_id: Optional[str] = None
    file_format: str = "csv"
    rules: Optional[List[Dict[str, Any]]] = None
    rule_set_id: Optional[str] = None
    result_mode: str = "errors_only"
    sheet_names: Optional[List[str]] = None
    header_row: Optional[int] = None
    #: Column label -> ``SECTION.field``. Errors report both, so a frontend can
    #: key on the path the template is configured with instead of the label.
    field_paths: Optional[Dict[str, str]] = None


@dataclass(kw_only=True)
class DataValidationResult:
    success: bool
    result: Optional[ValidationResult] = None  # Changed 'data' to 'result' to match JSON spec
    message: str = ""


class DataValidationUseCase:
    def __init__(
        self,
        logger: ILogger,
        validator: IValidatorProvider,
        rule_repo: IRuleRepository,
        storage: IFileStorageProvider,
        odata_parser: IODataParser
    ):
        self.logger = logger
        self.validator = validator
        self.rule_repo = rule_repo
        self.storage = storage
        self.odata_parser = odata_parser

    async def execute(self, command: DataValidationCommand) -> DataValidationResult:
        self.logger.info(f"Executing DataValidationUseCase for file: {command.file_id}")

        raw_rules = []
        if command.rules:
            self.logger.debug(f"Using {len(command.rules)} inline rules from request body.")
            raw_rules = command.rules
        elif command.rule_set_id:
            self.logger.debug(f"Fetching RuleSet ID: {command.rule_set_id} from database...")
            rule_set = await self.rule_repo.find_by_id(command.rule_set_id)
            if not rule_set:
                return DataValidationResult(success=False, message=f"RuleSet {command.rule_set_id} not found")
            raw_rules = rule_set.rules
            self.logger.info(f"Loaded {len(raw_rules)} rules from RuleSet '{rule_set.name}'.")
        else:
            return DataValidationResult(success=False, message="Rules or rule_set_id must be provided")

        domain_rules = [ValidationRule(
            rule_name=r.get("rule_name", "Unnamed Rule"),
            type=r.get("type", "expression"),
            params=r.get("params", {}),
            error_message=r.get("error_message", "Validation Failed"),
            description=r.get("description", "")
        ) for r in raw_rules]

        try:
            result_mode = self._normalize_result_mode(command.result_mode)
            val_result = await self.validator.validate_cloud_file(
                command.file_id,
                command.file_format,
                domain_rules,
                version_id=command.version_id,
                result_mode=result_mode,
                sheet_names=command.sheet_names,
                header_row=command.header_row,
                field_paths=command.field_paths,
            )

            # Format the exact message requested
            msg = f"Validation completed. Found {val_result.invalid_rows}/{val_result.total_rows} rows with errors."
            self.logger.info(msg)

            # Note the `result=val_result` here matches the `result` key in JSON
            return DataValidationResult(success=True, result=val_result, message=msg)
        except Exception as e:
            self.logger.error(f"Validation engine error: {e}")
            return DataValidationResult(success=False, message=str(e))

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

    def get_download_url(self, file_id: str, version_id: Optional[str] = None) -> str:
        self.logger.info(f"Generating download URL for {file_id}")
        return self.storage.generate_presigned_url(file_id, "download", version_id=version_id)

    def query_result_file(
        self,
        file_id: str,
        odata_query: str,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.logger.info(f"Executing OData Query on file {file_id}. Query: {odata_query}")
        ext = file_id.split('.')[-1].lower()
        if ext not in ['csv', 'json', 'xlsx']:
            ext = 'csv'
        df = self.storage.download_and_read(file_id, ext, version_id=version_id)
        return self.odata_parser.parse_and_format(df, odata_query)
