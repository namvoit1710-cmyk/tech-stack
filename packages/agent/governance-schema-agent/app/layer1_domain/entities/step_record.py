import json
import re
from dataclasses import dataclass, fields
from typing import Optional, get_args, Union
from .enums import Validity, AssignType, ContributeResolutionStrategy, ApprovalResolutionStrategy, ProcessExecutionMode, ApprovalProcessAction
from .section_definition import validate_parts, SectionDefinition


@dataclass
class StepRecord:
    FIELD_LABELS = {
         "description": "the description about the step",
        "assignToId": "who to assign this step to",
        "allowActions": "What action type this step belongs to"
    }
    MESSAGE_ALIASES = {
        "Find suitable section": "Found suitable section",
        "No section mentioned": "Wrong section",
        "No section mention": "Wrong section",
        "No suitable option": "Wrong section",
    }
    CASE_VALIDITY = {
        "Not related": Validity.INCORRECT,
        "Wrong section": Validity.INCORRECT,
        "Found suitable section": Validity.CORRECT,
        "Required all data or all fields": Validity.CORRECT,
        "Default step": Validity.CORRECT,
        "No need section": Validity.CORRECT,
    }
    CASE_SKIP = {
        "Not related": False,
        "Wrong section": False,
        "Found suitable section": False,
        "Required all data or all fields": False,
        "Default step": True,
        "No need section": False,
    }

    description: str
    stepId: Optional[int]
    stepOrder: Optional[int]
    parts: Optional[str]
    assignType: Optional[AssignType]
    assignToId: str
    allowActions : list[ApprovalProcessAction]
    contributeResolutionStrategy: Optional[ContributeResolutionStrategy]
    approvalResolutionStrategy: Optional[ApprovalResolutionStrategy]
    executionMode: Optional[ProcessExecutionMode]
    nextSteps: Optional[str]
    skip: Optional[bool]
    message: Optional[str] = None
    valid: Optional[Validity] = Validity.CORRECT
    step_default: Optional[bool] = False

    @staticmethod
    def normalize_message_label(data: dict) -> str:
        if bool(data.get("step_default", False)):
            return "Default step"

        raw_message = str(data.get("message", "")).strip()
        return StepRecord.MESSAGE_ALIASES.get(raw_message, raw_message)

    @staticmethod
    def normalize_parts(parts: str | list | None) -> str:
        if parts is None:
            return "[]"
        if isinstance(parts, str):
            return parts
        return json.dumps(parts)

    def to_dict(self) -> dict:
        def enum_value(value):
            return value.value if hasattr(value, "value") else value

        return {
            "description": self.description,
            "stepId": self.stepId,
            "stepOrder": self.stepOrder,
            "valid": enum_value(self.valid),
            "message": self.message,
            "parts": self.normalize_parts(self.parts),
            "assignType": enum_value(self.assignType),
            "assignToId": self.assignToId,
            "allowActions": enum_value(self.allowActions),
            "contributeResolutionStrategy": enum_value(self.contributeResolutionStrategy),
            "approvalResolutionStrategy": enum_value(self.approvalResolutionStrategy),
            "executionMode": enum_value(self.executionMode),
            "nextSteps": self.nextSteps,
            "skip": self.skip,
            "step_default": self.step_default,
        }

    @staticmethod
    def validate_required_fields(data: dict) -> tuple[Validity, Optional[str]]:
        """
        Validate required fields for StepRecord based on class annotations.
        Returns Validity.CORRECT if all required fields are valid, otherwise Validity.INCORRECT
        together with the first missing or invalid field name.
        """
        if bool(data.get("step_default", False)):
            return Validity.CORRECT, None

        # Helper to parse enums with None fallback
        def parse_enum(enum_cls, value):
            if value is None:
                return None
            value = str(value).strip()
            if not value:
                return None
            return enum_cls(value.upper())

        # Iterate over class fields to validate required fields
        for field in fields(StepRecord):
            field_name = field.name
            field_type = field.type

            # Skip validation for optional fields
            if getattr(field_type, "__origin__", None) is Union and type(None) in get_args(field_type):
                continue

            # Validate required fields
            value = data.get(field_name, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                return Validity.INCORRECT, field_name

            # Additional validation for enums
            # if field_type in [ProcessType]:
            #     if parse_enum(field_type, value) is None:
            #         return Validity.INCORRECT, field_name

        return Validity.CORRECT, None
    
    @staticmethod
    def validate_data_section(data_sections: dict) -> Validity:
        if not data_sections:
            return Validity.INCORRECT
        return Validity.CORRECT

    @staticmethod
    def from_dict(data: dict, allowed_sections: list[SectionDefinition] = None):
        try:
            allowed_sections = allowed_sections or []
            parts_raw = StepRecord.normalize_parts(data.get("parts", "[]"))
            parts = json.loads(parts_raw)
            if not isinstance(parts, list):
                raise ValueError("parts must be a JSON array string")

            # Helper to parse enums with None fallback
            def parse_enum(enum_cls, value):
                if value is None:
                    return None
                value = str(value).strip()
                if not value:
                    return None
                return enum_cls(value.upper())
            
            sections = {s.section for s in allowed_sections}
            message = StepRecord.normalize_message_label(data)
            valid = StepRecord.CASE_VALIDITY.get(message)
            skip = StepRecord.CASE_SKIP.get(message, bool(data.get("skip", False)))
            normalized_parts = "[]"

            if message == "Found suitable section":
                if not allowed_sections or not parts or any(
                    not isinstance(part, dict) or part.get("section", "") not in sections
                    for part in parts
                ):
                    message = "Wrong section"
                    valid = Validity.INCORRECT
                    skip = False
                else:
                    valid = Validity.CORRECT
                    normalized_parts = validate_parts(parts_raw, allowed_sections)
            elif message in StepRecord.CASE_VALIDITY:
                valid = StepRecord.CASE_VALIDITY[message]
            else:
                if allowed_sections and parts and all(
                    isinstance(part, dict) and part.get("section", "") in sections
                    for part in parts
                ):
                    message = "Found suitable section"
                    valid = Validity.CORRECT
                    skip = False
                    normalized_parts = validate_parts(parts_raw, allowed_sections)
                else:
                    message = "Wrong section"
                    valid = Validity.INCORRECT
                    skip = False


            return StepRecord(
                valid=valid,
                message=message,
                parts=normalized_parts,
                description= re.sub(r'Line\s+\d+:\s*', '', data.get("description", "")),
                stepId=int(data.get("stepId")),
                stepOrder=int(data.get("stepOrder")),
                assignType=parse_enum(AssignType, data.get("assignType")),
                assignToId=data.get("assignToId", ""),
                allowActions=json.dumps([ApprovalProcessAction(item) for item in data.get("allowActions")]) if data.get("allowActions") is not None else None,
                contributeResolutionStrategy=parse_enum(ContributeResolutionStrategy, data.get("contributeResolutionStrategy")),
                approvalResolutionStrategy=parse_enum(ApprovalResolutionStrategy, data.get("approvalResolutionStrategy")),
                executionMode=parse_enum(ProcessExecutionMode, data.get("executionMode")),
                nextSteps=data.get("nextSteps") or "",
                skip=skip,
                step_default=bool(data.get("step_default", False))
            )

        except ValueError as e:
            raise ValueError(f"Enum validation error: {e}")