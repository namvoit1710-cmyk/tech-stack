import json
import time
from typing import Any, Optional

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField, FieldConfig, ValidationRule, OutputType,
    FieldControl, FieldWrapper,
    text_field, number_field, select_field, switch_field,
    array_field,
)
from app.layer1_domain.value_objects.mapping_field_type import MappingFieldType
from app.layer1_domain.value_objects.mapping_mode import MappingMode
from app.layer1_domain.entities.mapping_rule import ArrayMappingConfig, MappingRule
from app.layer2_application.interfaces.mapping_engine_interface import IMappingEngine


# -- Reusable column definitions for mapping rule tables ----------------------

def _mapping_rule_columns(
    source_placeholder: str = "response_body.data.name",
    target_placeholder: str = "customer_name",
    default_placeholder: str = "Fallback if source is missing",
) -> list[AutoFormField]:
    return [
        text_field("source_path", "Source Path", placeholder=source_placeholder,
                   rules=[ValidationRule(method="required")]),
        text_field("target_field", "Target Field", placeholder=target_placeholder,
                   rules=[ValidationRule(method="required")]),
        select_field("field_type", "Type",
                     options=[
                         (MappingFieldType.STRING.value, "String"),
                         (MappingFieldType.NUMBER.value, "Number"),
                         (MappingFieldType.BOOLEAN.value, "Boolean"),
                         (MappingFieldType.OBJECT.value, "Object"),
                         (MappingFieldType.ARRAY.value, "Array"),
                     ]),
        text_field("default_value", "Default Value", placeholder=default_placeholder),
        select_field("mapping_mode", "Mode",
                     options=[
                         (MappingMode.DIRECT.value, "Direct"),
                         (MappingMode.CONSTANT.value, "Constant"),
                     ]),
    ]


# -- Input Schema -------------------------------------------------------------

_INPUT_FIELDS: list[AutoFormField] = [
    AutoFormField(
        key="source_data",
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Source Data", "required": True},
            control_props={"placeholder": '{"response_body": {"data": [...]}}'},
            rules=[ValidationRule(method="required")],
        ),
    ),
    # SA-1812 (G1): the flagship field-to-field mapping is now a first-class form
    # control. The engine has always read top-level ``mapping_rules``
    # (``execute()`` -> ``_parse_mapping_rules``), but it was absent from the input
    # schema, so a builder could only reach it via raw JSON on the node's input
    # binding. Surfacing it here as an array table makes "map field A -> field B"
    # authorable in the guided form — no raw JSON front door.
    array_field(
        "mapping_rules", "Field Mappings",
        item_fields=_mapping_rule_columns(),
    ),
    switch_field("strict_mode", "Strict Mode", default=False),
    # SA-1813: explicit value-handling policies (optional; blank = derive from
    # Strict Mode). on_missing decides what to do when a source path is missing;
    # on_type_error when a value can't be coerced; fail_on_error turns any
    # recorded error into a task failure.
    select_field("on_missing", "On Missing Source",
                 options=[("skip", "Skip field"), ("null", "Write null"), ("error", "Record error")]),
    select_field("on_type_error", "On Type Error",
                 options=[("error", "Record error"), ("null", "Write null"), ("keep", "Keep raw value")]),
    switch_field("fail_on_error", "Fail On Error", default=False),
    array_field(
        "array_mappings", "Array Mappings",
        item_fields=[
            text_field("source_array_path", "Source Array Path",
                       placeholder="response_body.data.customers",
                       rules=[ValidationRule(method="required")]),
            text_field("target_field", "Target Field",
                       placeholder="mapped_customers",
                       rules=[ValidationRule(method="required")]),
            select_field("output_mode", "Output Mode",
                         options=[
                             ("objects", "Array(value) to Array(object)"),
                             ("values", "Array(object) to Array(value)"),
                         ]),
            text_field("pluck_field", "Pluck Field",
                       placeholder="a  (used when output_mode=values)"),
            array_field(
                "item_mappings", "Item Mappings",
                item_fields=_mapping_rule_columns(
                    source_placeholder="full_name",
                    target_placeholder="name",
                    default_placeholder="",
                ),
            ),
        ],
    ),
]

MAPPING_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


# -- Output Schema ------------------------------------------------------------

_OUTPUT_FIELDS: list[AutoFormField] = [
    AutoFormField(
        key="mapped_data",
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Mapped Data", "required": True},
            control_props={"readonly": True},
            rules=[ValidationRule(method="required")],
        ),
    ),
    number_field("applied_rules_count", "Applied Rules Count",
                 readonly=True,
                 rules=[ValidationRule(method="required")]),
    AutoFormField(
        key="skipped_rules",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Skipped Rules"},
            control_props={"readonly": True},
        ),
    ),
    AutoFormField(
        key="errors",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Errors"},
            control_props={"readonly": True},
        ),
    ),
]

MAPPING_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Use Case -----------------------------------------------------------------

class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        mapping_engine: Optional[IMappingEngine] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self.mapping_engine = mapping_engine

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        self.logger.info(
            "Executing mapping data task",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("mapping_data_task_requests", 1)

        start: float = time.monotonic()

        try:
            inputs: dict[str, Any] = request.inputs

            # -- Resolve source data --
            source_data: Any = inputs.get("source_data")
            if source_data is None:
                raise ValueError("'source_data' is required.")
            if isinstance(source_data, str):
                source_data = json.loads(source_data)
            if not isinstance(source_data, dict):
                raise ValueError("'source_data' must be a JSON object.")

            # -- Resolve strict mode --
            strict_mode: bool = bool(inputs.get("strict_mode", False))

            # SA-1813: value-handling policies (optional; None preserves the
            # strict_mode-derived defaults). fail_on_error promotes a run that
            # produced any per-rule error to a task ERROR (default: SUCCESS).
            on_missing = inputs.get("on_missing") or None
            on_type_error = inputs.get("on_type_error") or None
            fail_on_error: bool = bool(inputs.get("fail_on_error", False))

            # -- Parse mapping rules --
            raw_rules: list[dict[str, Any]] = inputs.get("mapping_rules", [])
            mapping_rules: list[MappingRule] = self._parse_mapping_rules(raw_rules)

            # -- Parse array mappings --
            raw_array_mappings: list[dict[str, Any]] = inputs.get("array_mappings", [])
            array_mappings: list[ArrayMappingConfig] = self._parse_array_mappings(raw_array_mappings)

            # -- Resolve engine --
            if self.mapping_engine is None:
                from app.layer4_frameworks.providers.default_mapping_engine import DefaultMappingEngine
                engine: IMappingEngine = DefaultMappingEngine()
            else:
                engine = self.mapping_engine

            # -- Execute mapping --
            self.logger.info(
                f"Mapping {len(mapping_rules)} rules + {len(array_mappings)} array mappings",
                strict_mode=strict_mode,
            )
            result: dict[str, Any] = engine.map_data(
                source_data=source_data,
                mapping_rules=mapping_rules,
                array_mappings=array_mappings,
                strict_mode=strict_mode,
                on_missing=on_missing,
                on_type_error=on_type_error,
            )

            duration_ms: float = (time.monotonic() - start) * 1000

            self.monitor.track("mapping_data_task_duration_ms", duration_ms)
            self.monitor.track("mapping_data_task_success", 1)

            outputs: dict[str, Any] = {
                "mapped_data": result["mapped_data"],
                "applied_rules_count": result["applied_rules_count"],
                "skipped_rules": result["skipped_rules"],
                "errors": result["errors"],
            }

            # SA-1813: opt-in strictness — surface a partially-broken mapping as a
            # task ERROR instead of a SUCCESS with errors buried in the output.
            if fail_on_error and result["errors"]:
                self.monitor.track("mapping_data_task_error", 1)
                return ExecuteTaskResult(
                    task_id=request.task_id,
                    status=TaskStatus.ERROR,
                    outputs=outputs,
                    error=(
                        f"{len(result['errors'])} mapping error(s): "
                        + "; ".join(str(e) for e in result["errors"][:5])
                    ),
                    duration_ms=duration_ms,
                )

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS,
                outputs=outputs,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.error("Mapping data task failed", task_id=request.task_id, error=str(e))
            self.monitor.track("mapping_data_task_error", 1)

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                error=str(e),
                duration_ms=duration_ms,
            )

    def _parse_mapping_rules(self, raw_rules: list[dict[str, Any]]) -> list[MappingRule]:
        """Parse raw input dicts into MappingRule dataclass instances."""
        valid_field_types = {e.value for e in MappingFieldType}
        valid_mapping_modes = {e.value for e in MappingMode}
        rules: list[MappingRule] = []
        for raw in raw_rules:
            source_path: str = raw.get("source_path", "")
            target_field: str = raw.get("target_field", "")
            if not source_path and raw.get("mapping_mode") != MappingMode.CONSTANT.value:
                continue
            if not target_field:
                continue

            # -- Validate field_type --
            field_type: str = raw.get("field_type", MappingFieldType.STRING.value)
            if field_type not in valid_field_types:
                self.logger.warning(
                    f"Invalid field_type '{field_type}' for target_field '{target_field}'. "
                    f"Must be one of: {', '.join(valid_field_types)}. Defaulting to '{MappingFieldType.STRING.value}'."
                )
                field_type = MappingFieldType.STRING.value

            # -- Validate mapping_mode --
            mapping_mode: str = raw.get("mapping_mode", MappingMode.DIRECT.value)
            if mapping_mode not in valid_mapping_modes:
                self.logger.warning(
                    f"Invalid mapping_mode '{mapping_mode}' for target_field '{target_field}'. "
                    f"Must be one of: {', '.join(valid_mapping_modes)}. Defaulting to '{MappingMode.DIRECT.value}'."
                )
                mapping_mode = MappingMode.DIRECT.value

            rules.append(MappingRule(
                source_path=source_path,
                target_field=target_field,
                field_type=field_type,
                default_value=raw.get("default_value"),
                mapping_mode=mapping_mode,
            ))
        return rules

    def _parse_array_mappings(self, raw_array_mappings: list[dict[str, Any]]) -> list[ArrayMappingConfig]:
        """Parse raw input dicts into ArrayMappingConfig instances."""
        configs: list[ArrayMappingConfig] = []
        for raw in raw_array_mappings:
            source_array_path: str = raw.get("source_array_path", "")
            target_field: str = raw.get("target_field", "")
            if not source_array_path or not target_field:
                continue
            output_mode: str = raw.get("output_mode", "objects")
            if output_mode not in ("objects", "values"):
                output_mode = "objects"
            pluck_field: Optional[str] = raw.get("pluck_field") or None
            raw_item_mappings: list[dict[str, Any]] = raw.get("item_mappings", [])
            item_mappings: list[MappingRule] = self._parse_mapping_rules(raw_item_mappings)
            configs.append(ArrayMappingConfig(
                source_array_path=source_array_path,
                target_field=target_field,
                item_mappings=item_mappings,
                output_mode=output_mode,
                pluck_field=pluck_field,
            ))
        return configs
