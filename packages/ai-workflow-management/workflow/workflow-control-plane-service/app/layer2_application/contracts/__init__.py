"""Shared pydantic DTOs for the v2 node catalog API.

Phase 0 scope: module exists and is importable; not yet wired into any
endpoint. Phase 1 wires these shapes into /v2/node-palette and
/v2/node-definitions/{id}.
"""

from app.layer2_application.contracts.form_schema import (
    ConditionAction,
    ConditionConfig,
    ConditionOperator,
    ControlProps,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    FormField,
    FormSchema,
    OutputType,
    SelectOption,
    ValidationMethod,
    ValidationRule,
    WrapperProps,
)
from app.layer2_application.contracts.node_definition_dto import (
    NodeCapability,
    NodeCategoryInfo,
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodePort,
    NodePorts,
    NodeProviderKind,
    NodeRuntime,
    NodeVisibility,
)
from app.layer2_application.contracts.providers import NodeProvider

__all__ = [
    # Enums
    "ConditionAction",
    "ConditionOperator",
    "FieldControl",
    "FieldWrapper",
    "NodeProviderKind",
    "NodeRuntime",
    "NodeVisibility",
    "OutputType",
    "ValidationMethod",
    # Form schema
    "ConditionConfig",
    "ControlProps",
    "FieldConfig",
    "FormField",
    "FormSchema",
    "SelectOption",
    "ValidationRule",
    "WrapperProps",
    # Node catalog
    "NodeCapability",
    "NodeCategoryInfo",
    "NodeDefinitionDetail",
    "NodeDefinitionSummary",
    "NodePort",
    "NodePorts",
    # Provider protocol
    "NodeProvider",
]
