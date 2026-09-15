"""SA-1078 — multi-type node registry for the mapping-data-worker.

Exposes TWO node types from one worker process (worker-sdk ``node_types=[...]``):

* ``mapping_data``  — the existing Mapping Data node, UNCHANGED. Its handler is a
  thin adapter that re-runs the existing ``ExecuteTaskUseCase`` + ``DefaultMappingEngine``,
  so behavior is byte-for-byte the same (parity guarded by tests).
* ``array_transform`` — NEW Array Value Transform (SA-1078), backed by
  ``ArrayTransformEngine`` (two-array match/merge).

Adding this file + wiring it in ``main.py`` is purely additive to the worker; the
mapping node keeps its worker_type, schema, name/icon/color and engine.
"""
from __future__ import annotations

import json
from typing import Any

from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField, FieldConfig, ValidationRule, OutputType, FieldControl, FieldWrapper,
    text_field, select_field, array_field, switch_field, number_field,
)

from app.layer4_frameworks.config import settings
from app.layer4_frameworks.providers.array_transform_engine import ArrayTransformEngine
from app.layer4_frameworks.providers.aggregate_engine import AggregateEngine
from app.layer4_frameworks.providers.default_mapping_engine import DefaultMappingEngine, MISSING
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase, ExecuteTaskCommand, MAPPING_INPUT_SCHEMA, MAPPING_OUTPUT_SCHEMA,
    _mapping_rule_columns,
)

_MODE_OPTIONS = [("replace", "Replace"), ("enrich", "Enrich"), ("merge", "Merge")]


def _code_array_field(key: str, label: str, placeholder: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label, "required": True},
            control_props={"placeholder": placeholder},
            rules=[ValidationRule(method="required")],
        ),
    )


def _readonly(key: str, label: str, output_type: OutputType) -> AutoFormField:
    return AutoFormField(
        key=key, output_type=output_type,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA, field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label}, control_props={"readonly": True},
        ),
    )


_ATX_PLACEHOLDER = '[{"field": "name", "value": "..."}]'

ARRAY_TRANSFORM_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    _code_array_field("array1", "Array 1 (target)", _ATX_PLACEHOLDER),
    _code_array_field("array2", "Array 2 (source)", _ATX_PLACEHOLDER),
    select_field("match_by", "Match By", options=[("field", "Field"), ("value", "Value")]),
    select_field("mode", "Mode", options=_MODE_OPTIONS),
    select_field("unmatched", "Unmatched (Array 1 only)", options=[("keep", "Keep"), ("drop", "Drop")]),
    select_field("extra", "Extra (Array 2 only)", options=[("ignore", "Ignore"), ("add", "Add")]),
    array_field(
        "field_overrides", "Field Overrides",
        item_fields=[
            text_field("field", "Field", placeholder="field name", rules=[ValidationRule(method="required")]),
            select_field("mode", "Mode", options=_MODE_OPTIONS),
        ],
    ),
]]

ARRAY_TRANSFORM_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    _readonly("result", "Result", OutputType.ARRAY),
    _readonly("changes", "Changes (old / new / status)", OutputType.ARRAY),
    _readonly("summary", "Summary", OutputType.OBJECT),
]]


def _as_item_list(v: Any) -> list[dict[str, Any]]:
    if v is None:
        return []
    if isinstance(v, str):
        v = json.loads(v)
    if isinstance(v, dict):
        return [v]
    if not isinstance(v, list):
        raise ValueError("expected a list of {field, value} objects")
    return v


def _array_transform_handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    engine = ArrayTransformEngine()
    array1 = _as_item_list(inputs.get("array1"))
    array2 = _as_item_list(inputs.get("array2"))
    global_mode = inputs.get("mode", "replace")
    overrides: dict[str, str] = {}
    for row in (inputs.get("field_overrides") or []):
        if isinstance(row, dict) and row.get("field"):
            overrides[row["field"]] = row.get("mode") or global_mode
    return engine.transform(
        array1, array2,
        match_by=inputs.get("match_by", "field"),
        mode=global_mode,
        field_overrides=overrides,
        unmatched=inputs.get("unmatched", "keep"),
        extra=inputs.get("extra", "ignore"),
    )


class _NoopLogger:
    def info(self, *a: Any, **k: Any) -> None: ...
    def error(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...
    def debug(self, *a: Any, **k: Any) -> None: ...


class _NoopMonitor:
    def track(self, *a: Any, **k: Any) -> None: ...


def _mapping_handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Adapter: run the EXISTING mapping use case unchanged (byte-for-byte parity)."""
    uc = ExecuteTaskUseCase(logger=_NoopLogger(), monitor=_NoopMonitor(), mapping_engine=DefaultMappingEngine())
    res = uc.execute(ExecuteTaskCommand(
        task_id=str(inputs.get("_task_id", "mapping")),
        action="execute",
        inputs=inputs,
        parameters=parameters or {},
    ))
    status = getattr(res.status, "value", res.status)
    if str(status).upper() in ("ERROR", "FAILED"):
        raise RuntimeError(res.error or "mapping task failed")
    return res.outputs


MAPPING_NODE_DEF = NodeTypeDefinition(
    worker_type=settings.WORKER_TYPE,            # "mapping_data" — unchanged
    name=settings.WORKER_NAME,
    description="Reshape a single source object/array via field mapping rules.",
    node_class=settings.WORKER_NODE_CLASS,
    icon=settings.WORKER_ICON,
    color=settings.WORKER_COLOR,
    tags=settings.get_tags_list(),
    input_schema=MAPPING_INPUT_SCHEMA,
    output_schema=MAPPING_OUTPUT_SCHEMA,
    handler=_mapping_handler,
)

ARRAY_TRANSFORM_NODE_DEF = NodeTypeDefinition(
    worker_type="array_transform",
    name="Array Value Transform",
    description="Map & transform values between two arrays (match by field/value; replace/enrich/merge).",
    node_class="TECHNICAL",
    icon="ArrowLeftRight",
    color="#8B5CF6",
    tags=["array", "transform", "merge", "technical"],
    input_schema=ARRAY_TRANSFORM_INPUT_SCHEMA,
    output_schema=ARRAY_TRANSFORM_OUTPUT_SCHEMA,
    handler=_array_transform_handler,
)

# -- mapping_iterate (SA-1814) — array -> array, index-aligned ----------------

ITERATE_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    AutoFormField(
        key="source_data",
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Source Data", "required": True},
            control_props={"placeholder": '{"response_body": {"data": {"items": [...]}}}'},
            rules=[ValidationRule(method="required")],
        ),
    ),
    text_field("source_array_path", "Source Array Path",
               placeholder="response_body.data.items",
               rules=[ValidationRule(method="required")]),
    select_field("output_mode", "Output Mode",
                 options=[("objects", "Array(object) to Array(object)"),
                          ("values", "Array(object) to Array(value)")]),
    text_field("pluck_field", "Pluck Field",
               placeholder="field to pluck (used when output_mode=values)"),
    array_field("item_mappings", "Item Mappings", item_fields=_mapping_rule_columns(
        source_placeholder="full_name", target_placeholder="name", default_placeholder="",
    )),
    switch_field("strict_mode", "Strict Mode", default=False),
]]

ITERATE_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    _readonly("result", "Result (array)", OutputType.ARRAY),
    number_field("applied_rules_count", "Applied Rules Count", readonly=True),
    _readonly("skipped_rules", "Skipped Rules", OutputType.ARRAY),
    _readonly("errors", "Errors", OutputType.ARRAY),
]]


def _iterate_handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Adapter for the Map-Array / For-Each node: run ONE array mapping through
    the existing use case (reuses its parse + DefaultMappingEngine, so item
    mapping + index-aligned values behave exactly like the mapping_data node's
    array_mappings). Output is the mapped array under ``result``."""
    array_mapping = {
        "source_array_path": inputs.get("source_array_path", ""),
        "target_field": "result",
        "item_mappings": inputs.get("item_mappings", []),
        "output_mode": inputs.get("output_mode", "objects"),
        "pluck_field": inputs.get("pluck_field"),
    }
    uc = ExecuteTaskUseCase(logger=_NoopLogger(), monitor=_NoopMonitor(), mapping_engine=DefaultMappingEngine())
    res = uc.execute(ExecuteTaskCommand(
        task_id=str(inputs.get("_task_id", "iterate")),
        action="execute",
        inputs={
            "source_data": inputs.get("source_data"),
            "array_mappings": [array_mapping],
            "strict_mode": inputs.get("strict_mode", False),
        },
        parameters=parameters or {},
    ))
    status = getattr(res.status, "value", res.status)
    if str(status).upper() in ("ERROR", "FAILED"):
        raise RuntimeError(res.error or "iterate task failed")
    md = res.outputs.get("mapped_data") or {}
    return {
        "result": md.get("result", []),
        "applied_rules_count": res.outputs.get("applied_rules_count", 0),
        "skipped_rules": res.outputs.get("skipped_rules", []),
        "errors": res.outputs.get("errors", []),
    }


ITERATE_NODE_DEF = NodeTypeDefinition(
    worker_type="mapping_iterate",
    name="Map Array / For-Each",
    description="Iterate a source array and map each item (index-aligned); output objects or plucked values.",
    node_class="TECHNICAL",
    icon="ArrowLeftRight",
    color=settings.WORKER_COLOR,
    tags=["array", "iterate", "map", "technical"],
    input_schema=ITERATE_INPUT_SCHEMA,
    output_schema=ITERATE_OUTPUT_SCHEMA,
    handler=_iterate_handler,
)

# -- mapping_aggregate (SA-1815) — N items -> grouped rows (fan-in reduce) ----

_AGG_OP_OPTIONS = [
    ("sum", "Sum"), ("count", "Count"), ("min", "Min"), ("max", "Max"),
    ("avg", "Average"), ("collect", "Collect (list)"), ("join", "Join (string)"),
]

AGGREGATE_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    AutoFormField(
        key="source_data", output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL, field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Source Data", "required": True},
            control_props={"placeholder": '{"orders": [{"region": "NA", "amount": 10}, ...]}'},
            rules=[ValidationRule(method="required")],
        ),
    ),
    text_field("source_array_path", "Source Array Path", placeholder="orders",
               rules=[ValidationRule(method="required")]),
    array_field("group_by", "Group By", item_fields=[
        text_field("field", "Field", placeholder="region"),
    ]),
    array_field("aggregations", "Aggregations", item_fields=[
        select_field("op", "Operation", options=_AGG_OP_OPTIONS),
        text_field("field", "Field", placeholder="amount  (omit for count)"),
        text_field("target", "Output Key", placeholder="total_amount"),
        text_field("separator", "Join Separator", placeholder=",  (join only)"),
    ]),
    switch_field("strict_mode", "Strict Mode", default=False),
]]

AGGREGATE_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    _readonly("result", "Result (grouped rows)", OutputType.ARRAY),
    number_field("group_count", "Group Count", readonly=True),
    _readonly("errors", "Errors", OutputType.ARRAY),
]]


def _aggregate_handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Aggregate / Reduce node: resolve the source array, then group + reduce via
    AggregateEngine. group_by empty -> one overall row; else one row per group."""
    source_data = inputs.get("source_data")
    if isinstance(source_data, str):
        source_data = json.loads(source_data)
    if not isinstance(source_data, dict):
        raise ValueError("'source_data' must be a JSON object")
    arr = DefaultMappingEngine()._get_nested_value(source_data, inputs.get("source_array_path", ""))
    if arr is MISSING or arr is None:
        arr = []
    if not isinstance(arr, list):
        raise ValueError(
            f"source_array_path '{inputs.get('source_array_path')}' did not resolve to an array"
        )
    group_by = [
        r.get("field") for r in (inputs.get("group_by") or [])
        if isinstance(r, dict) and r.get("field")
    ]
    aggregations = [
        r for r in (inputs.get("aggregations") or [])
        if isinstance(r, dict) and r.get("op")
    ]
    res = AggregateEngine().aggregate(
        arr, group_by, aggregations, strict_mode=bool(inputs.get("strict_mode", False)),
    )
    return {"result": res["result"], "group_count": res["group_count"], "errors": res["errors"]}


AGGREGATE_NODE_DEF = NodeTypeDefinition(
    worker_type="mapping_aggregate",
    name="Aggregate / Reduce",
    description="Reduce a source array into grouped rows: group_by + sum/count/min/max/avg/collect/join.",
    node_class="TECHNICAL",
    icon="ArrowLeftRight",
    color=settings.WORKER_COLOR,
    tags=["aggregate", "reduce", "group", "technical"],
    input_schema=AGGREGATE_INPUT_SCHEMA,
    output_schema=AGGREGATE_OUTPUT_SCHEMA,
    handler=_aggregate_handler,
)

NODE_TYPES = [MAPPING_NODE_DEF, ARRAY_TRANSFORM_NODE_DEF, ITERATE_NODE_DEF, AGGREGATE_NODE_DEF]
