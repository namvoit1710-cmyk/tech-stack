from dataclasses import dataclass
from datetime import datetime
from typing import Any


# =========================================================
# Workflow
# =========================================================

@dataclass
class Workflow:
    id: str
    name: str
    description: str | None = None


# =========================================================
# FieldRule
# =========================================================

@dataclass
class FieldRule:
    method: str
    message: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldRule":
        return cls(
            method=data["method"],        # required
            message=data.get("message"),  # optional
        )


# =========================================================
# FieldWrapperProps
# =========================================================

@dataclass
class FieldWrapperProps:
    label: str = ""
    description: str = ""
    required: bool = False
    isAdd: bool = False
    isDelete: bool = False
    size: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldWrapperProps":
        # Wrappers carry different props (label/description for form items;
        # isAdd/isDelete/size for table wrappers). Parse per-field so unknown keys from
        # other wrapper types are ignored instead of crashing a ``**`` splat.
        return cls(
            label=data.get("label", ""),
            description=data.get("description", ""),
            required=data.get("required", False),
            isAdd=data.get("isAdd", False),
            isDelete=data.get("isDelete", False),
            size=data.get("size"),
        )


# =========================================================
# FieldControlProps
# =========================================================

@dataclass
class FieldControlProps:
    placeholder: str | None = None
    valueKey: str | None = None
    labelKey: str | None = None
    options: list[dict[str, Any]] | None = None
    rowsLength: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldControlProps":
        return cls(
            placeholder=data.get("placeholder"),
            valueKey=data.get("valueKey"),
            labelKey=data.get("labelKey"),
            options=data.get("options"),
            rowsLength=data.get("rowsLength"),
        )


# =========================================================
# FieldConfig
# =========================================================

@dataclass
class FieldConfig:
    fieldWrapper: str
    wrapperProps: FieldWrapperProps
    fieldControl: str | None = None
    controlProps: FieldControlProps | None = None
    rules: list[FieldRule] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldConfig":
        control_props = data.get("controlProps")
        rules = data.get("rules")
        return cls(
            fieldWrapper=data["fieldWrapper"],                               # required
            wrapperProps=FieldWrapperProps.from_dict(data["wrapperProps"]),  # required
            fieldControl=data.get("fieldControl"),                           # optional (absent on wrappers)
            controlProps=(
                FieldControlProps.from_dict(control_props)
                if control_props is not None
                else None
            ),
            rules=(
                [FieldRule.from_dict(r) for r in rules] if rules is not None else None
            ),
        )


# =========================================================
# InputSchemaItem
# =========================================================

@dataclass
class InputSchemaItem:
    key: str
    outputType: str
    fieldConfig: FieldConfig
    fields: list["InputSchemaItem"] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InputSchemaItem":
        # ``fields`` holds nested sub-fields (e.g. an array/table node whose rows carry
        # their own field schema); parse recursively when present.
        nested = data.get("fields")
        return cls(
            key=data["key"],                                        # required
            outputType=data["outputType"],                          # required
            fieldConfig=FieldConfig.from_dict(data["fieldConfig"]),  # required
            fields=(
                [cls.from_dict(f) for f in nested] if nested is not None else None
            ),
        )


# =========================================================
# WorkNodeStatus
# =========================================================

@dataclass
class WorkNodeStatus:
    run_id: str
    node_id: str
    node_name: str
    node_type: str
    status: str

    input_schema: list[InputSchemaItem]
    edit_mode: str
    data_mode: str

    scalars: dict[str, Any]
    arrays: list[Any]

    main_file_id: str | None
    main_links: dict[str, Any]

    error: str | None
    error_code: str
    error_details: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkNodeStatus":
        # Core node/schema fields are required (``[]``); the fields the /schema endpoint does
        # not return (edit_mode/data_mode/scalars/arrays/main_file_id/main_links) and the
        # nullable ones (main_file_id/error) are optional (``.get()`` with a default).
        return cls(
            run_id=data["run_id"],
            node_id=data["node_id"],
            node_name=data["node_name"],
            node_type=data["node_type"],
            status=data["status"],
            input_schema=[InputSchemaItem.from_dict(i) for i in data["input_schema"]],
            edit_mode=data.get("edit_mode", ""),
            data_mode=data.get("data_mode", ""),
            scalars=data.get("scalars", {}),
            arrays=data.get("arrays", []),
            main_file_id=data.get("main_file_id"),
            main_links=data.get("main_links", {}),
            error=data.get("error"),
            error_code=data["error_code"],
            error_details=data["error_details"],
        )

    @classmethod
    def from_schema_response(
        cls, payload: dict[str, Any], run_id: str, node_id: str
    ) -> "WorkNodeStatus":
        """Build from a ``/runs/{run}/nodes/{node}/schema`` response.

        Shape (SA-890)::

            {"status": "ok", "data": {
                "schema": [ ...input_schema items... ],
                "node_state": "PENDING", "node_kind": "INPUT",
                "node": {"name": ..., "node_type": ..., "description": ...},
                "error": null, "error_code": "", "error_details": {}}}

        ``data`` and ``data.schema`` are required (indexed with ``[]``); the rest of the
        envelope is optional context (``.get()``). run_id/node_id come from the request path,
        not the body. Fields the /schema endpoint does not return are defaulted here so the
        dict handed to the strict from_dict is complete.
        """
        data = payload["data"]  # required
        node = data.get("node") or {}
        return cls.from_dict(
            {
                "run_id": run_id,
                "node_id": node_id,
                "node_name": node.get("name", ""),
                "node_type": node.get("node_type") or data.get("node_kind", ""),
                "status": data.get("node_state", ""),
                "input_schema": data["schema"],  # required
                "error": data.get("error"),
                "error_code": data.get("error_code", ""),
                "error_details": data.get("error_details") or {},
            }
        )


# =========================================================
# WorkflowRunInfoData
# =========================================================

@dataclass
class WorkflowRunInfoData:
    id: str
    workflow_id: str
    status: str

    started_at: datetime
    execution_mode: str  # "auto" | "manual"

    completed_at: datetime | None = None
    error: str | None = None
    output_data: dict | None = None


# =========================================================
# WorkflowRunInfoResponse
# =========================================================

@dataclass
class WorkflowRunInfoResponse:
    status: str
    data: WorkflowRunInfoData


    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowRunInfoResponse":
        return cls(
            status=data["status"],
            data=WorkflowRunInfoData(**data["data"]),
        )