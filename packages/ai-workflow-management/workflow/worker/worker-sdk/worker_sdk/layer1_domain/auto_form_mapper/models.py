"""Core data models mapping to auto-form TypeScript interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from .enums import ConditionAction, ConditionOperator, HttpMethod, OutputType


@dataclass
class CallApiConfig:
    """Maps to ICallApiConfig."""
    endpoint: Optional[str] = None
    method: Optional[HttpMethod] = None
    body: Optional[dict[str, Any]] = None
    query_params: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.endpoint is not None:
            result["endpoint"] = self.endpoint
        if self.method is not None:
            result["method"] = self.method.value
        if self.body is not None:
            result["body"] = self.body
        if self.query_params is not None:
            result["queryParams"] = self.query_params
        return result


@dataclass
class ConditionConfig:
    """Maps to IConditionConfig."""
    field_key: str
    condition_value: Union[str, list[str], int, float, bool]
    action: ConditionAction = ConditionAction.INVISIBLE
    operator: ConditionOperator = ConditionOperator.EQUALS

    def to_dict(self) -> dict[str, Any]:
        return {
            "fieldKey": self.field_key,
            "conditionValue": self.condition_value,
            "action": self.action.value,
            "operator": self.operator.value,
        }


@dataclass
class ValidationRule:
    """Maps to IValidationRules array element: {method, value?, message?}."""
    method: str
    value: Optional[Union[str, int, float]] = None
    message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"method": self.method}
        if self.value is not None:
            result["value"] = self.value
        if self.message is not None:
            result["message"] = self.message
        return result


@dataclass
class FieldConfig:
    """Maps to IFieldConfig."""
    field_wrapper: Optional[str] = None
    wrapper_props: Optional[dict[str, Any]] = None
    field_control: Optional[str] = None
    control_props: Optional[dict[str, Any]] = None
    rules: Optional[list[ValidationRule]] = None
    condition: Optional[ConditionConfig] = None
    initial_api_config: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.field_wrapper is not None:
            result["fieldWrapper"] = self.field_wrapper
        if self.wrapper_props is not None:
            result["wrapperProps"] = self.wrapper_props
        if self.field_control is not None:
            result["fieldControl"] = self.field_control
        if self.control_props is not None:
            result["controlProps"] = self.control_props
        if self.rules:
            result["rules"] = [r.to_dict() for r in self.rules]
        if self.condition is not None:
            result["condition"] = self.condition.to_dict()
        if self.initial_api_config is not None:
            result["initialApiConfig"] = self.initial_api_config
        return result


@dataclass
class AutoFormField:
    """Maps to IField."""
    key: str
    output_type: OutputType
    field_config: FieldConfig
    fields: Optional[list[AutoFormField]] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "key": self.key,
            "outputType": self.output_type.value,
            "fieldConfig": self.field_config.to_dict(),
        }
        if self.fields:
            result["fields"] = [f.to_dict() for f in self.fields]
        return result


@dataclass
class Schema:
    """Maps to ISchema. Root-level schema object."""
    fields: list[AutoFormField]

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [f.to_dict() for f in self.fields]}

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        import json
        return json.dumps(self.to_dict(), indent=indent)
