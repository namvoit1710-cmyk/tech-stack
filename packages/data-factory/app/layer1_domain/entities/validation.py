from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass(kw_only=True)
class ValidationRule:
    rule_name: str
    type: str
    params: Dict[str, Any] = field(default_factory=dict)
    error_message: str = "Validation failed"
    description: str = ""


@dataclass(kw_only=True)
class ODataData:
    rule_name: str
    violate: int
    error_message: str
    error_code: Dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class ODataContent:
    data: List[ODataData]
    result_file_url: str = ""
    result_file_id: str = ""
    result_version_id: Optional[str] = None
    type: str = "getDetailError"
    fields: List[str] = field(default_factory=lambda: ["rule_name", "violate", "error_message"])


@dataclass(kw_only=True)
class ValidationResult:
    total_rows: int
    valid_rows: int
    invalid_rows: int
    odata: ODataContent
