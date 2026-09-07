from typing import Protocol, List, Optional
from app.layer1_domain.entities.validation import ValidationRule, ValidationResult


class IValidatorProvider(Protocol):
    async def validate_cloud_file(
        self,
        file_path: str,
        file_format: str,
        rules: List[ValidationRule],
        version_id: Optional[str] = None,
        result_mode: str = "errors_only",
        sheet_names: Optional[List[str]] = None,
        header_row: Optional[int] = None,
    ) -> ValidationResult: ...
