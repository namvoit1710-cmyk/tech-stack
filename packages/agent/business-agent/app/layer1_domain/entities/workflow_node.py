from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Rule:
    method: str


@dataclass
class WrapperProps:
    label: str = ""
    description: str = ""
    required: bool = False


@dataclass
class ControlProps:
    placeholder: str = ""
    valueKey: Optional[str] = None
    labelKey: Optional[str] = None
    options: Optional[List[Dict[str, Any]]] = None


@dataclass
class FieldConfig:
    fieldWrapper: str
    fieldControl: str
    wrapperProps: WrapperProps
    controlProps: ControlProps
    rules: List[Rule] = field(default_factory=list)


@dataclass
class SchemaField:
    key: str
    outputType: str
    fieldConfig: FieldConfig


@dataclass
class Node:
    name: str
    node_type: str
    description: str
    worker_type: str


@dataclass
class InputNodeData:
    schema: List[SchemaField]
    schema_version: str
    schema_source: str
    node_kind: str
    node_state: str
    node: Node
    error: Optional[Any]
    error_code: str
    error_details: Dict[str, Any]


@dataclass
class InputNodeResponse:
    status: str
    data: InputNodeData

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "InputNodeResponse":
        data = payload["data"]

        schema = []
        for field_data in data["schema"]:
            fc = field_data["fieldConfig"]

            schema.append(
                SchemaField(
                    key=field_data["key"],
                    outputType=field_data.get("outputType", ""),
                    fieldConfig=FieldConfig(
                        fieldWrapper=fc.get("fieldWrapper", ""),
                        fieldControl=fc.get("fieldControl", ""),
                        wrapperProps=WrapperProps(**fc.get("wrapperProps", {})),
                        controlProps=ControlProps(**fc.get("controlProps", {})),
                        rules=[Rule(**rule) for rule in fc.get("rules", [])],
                    ),
                )
            )

        return cls(
            status=payload.get("status", ""),
            data=InputNodeData(
                schema=schema,
                schema_version=data.get("schema_version", ""),
                schema_source=data.get("schema_source", ""),
                node_kind=data.get("node_kind", ""),
                node_state=data.get("node_state", ""),
                node=Node(**data.get("node", {})),
                error=data.get("error"),
                error_code=data.get("error_code", ""),
                error_details=data.get("error_details", {}),
            ),
        )


from dataclasses import dataclass
from typing import Any, Dict, Optional, List


@dataclass
class Operation:
    operation: str
    row_id: Optional[str]
    data: Dict[str, str]


@dataclass
class Artifact:
    artifact_id: str
    operations: List[Operation]


@dataclass
class ArtifactPayload:
    artifacts: List[Artifact]


def build_artiface_payload(filled_data: Dict[str, str]) -> ArtifactPayload:
    return ArtifactPayload(
        artifacts=[
            Artifact(
                artifact_id="__root__",
                operations=[
                    Operation(operation="insert", row_id=None, data=filled_data)
                ],
            )
        ]
    )


@dataclass
class NodePatchData:
    etag: str

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NodePatchData":
        return cls(etag=d.get("etag", ""))


@dataclass
class NodePatchResponse:
    status: str
    data: NodePatchData

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NodePatchResponse":
        return cls(
            status=d.get("status", ""), data=NodePatchData.from_dict(d.get("data", {}))
        )


@dataclass
class SchemaRef:
    url: str
    etag: str

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SchemaRef":
        return cls(url=d.get("url", ""), etag=d.get("etag", ""))


@dataclass
class PreviewRow:
    row_id: str
    output_content: Optional[Any]
    metadata_type: Optional[str]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PreviewRow":
        return cls(
            row_id=d.get("__row_id", ""),
            output_content=d.get("output.content"),
            metadata_type=d.get("metadata.type"),
        )


@dataclass
class ArtifactRoot:
    artifact_id: str
    file_id: str
    columns: List[str]
    parent_row_key_column: Optional[str]
    children: List[Any]
    artifact_type: Optional[str]
    content_type: str
    preview_rows: List[PreviewRow] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArtifactRoot":
        return cls(
            artifact_id=d.get("artifact_id", ""),
            file_id=d.get("file_id", ""),
            columns=d.get("columns", []),
            parent_row_key_column=d.get("parent_row_key_column"),
            children=d.get("children", []),
            artifact_type=d.get("artifact_type"),
            content_type=d.get("content_type", ""),
            preview_rows=[
                PreviewRow.from_dict(row) for row in d.get("preview_rows", [])
            ],
        )


@dataclass
class Output:
    etag: str
    version: int
    root: ArtifactRoot
    output_port: str
    taken_edge_ports: Optional[Any]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Output":
        return cls(
            etag=d.get("etag", ""),
            version=d.get("version", 0),
            root=ArtifactRoot.from_dict(d.get("root", {})),
            output_port=d.get("output_port", ""),
            taken_edge_ports=d.get("taken_edge_ports"),
        )


@dataclass
class TaskNodeOutputData:
    run_id: str
    node_id: str
    task_id: str
    node_kind: str
    node_state: str
    schema_ref: SchemaRef
    data: Optional[Any]
    output: Optional[Output]
    produced_artifacts: List[Any]
    etag: str
    node_error: Optional[Any]
    error: Optional[Any]
    error_code: str
    error_details: Dict[str, Any]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskNodeOutputData":
        return cls(
            run_id=d.get("run_id", ""),
            node_id=d.get("node_id", ""),
            task_id=d.get("task_id", ""),
            node_kind=d.get("node_kind", ""),
            node_state=d.get("node_state", ""),
            schema_ref=SchemaRef.from_dict(d.get("schema_ref", {})),
            data=d.get("data"),
            output=Output.from_dict(d["output"]) if d.get("output") else None,
            produced_artifacts=d.get("produced_artifacts", []),
            etag=d.get("etag", ""),
            node_error=d.get("node_error"),
            error=d.get("error"),
            error_code=d.get("error_code", ""),
            error_details=d.get("error_details", {}),
        )


@dataclass
class TaskNodeOutputResponse:
    status: str
    data: TaskNodeOutputData

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskNodeOutputResponse":
        return cls(
            status=d.get("status", ""),
            data=TaskNodeOutputData.from_dict(d.get("data", {})),
        )
