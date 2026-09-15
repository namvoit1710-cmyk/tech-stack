"""FILE_UPLOAD spec — pause for a user file upload."""

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.model import (
    ConfigField,
    FieldType,
    NodeSpec,
    OutputField,
    Widget,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

FILE_UPLOAD_SPEC = register(NodeSpec(
    node_type=NodeType.FILE_UPLOAD,
    name="File Upload",
    description="Pauses workflow to allow a user to upload a file.",
    category="human_in_the_loop.file",
    runtime="pending",
    node_class=NodeClass.BUSINESS,
    icon="Upload",
    color="#3B82F6",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="instruction", type=FieldType.STRING, label="Instruction",
            widget=Widget.TEXTAREA,
            placeholder="Upload a file for processing.",
        ),
        ConfigField(
            key="artifact_key", type=FieldType.STRING, label="Artifact Key",
            placeholder="uploaded_file",
        ),
        ConfigField(
            key="use_agent", type=FieldType.STRING, label="Use Agent",
            placeholder="agent-1, agent-2...", required=True,
        ),
    ),
    output_fields=(
        OutputField("file_id", FieldType.STRING, "File ID"),
        OutputField("file_name", FieldType.STRING, "File Name"),
        OutputField("file_size", FieldType.NUMBER, "File Size"),
        OutputField("content_type", FieldType.STRING, "Content Type"),
    ),
))


@dataclass(frozen=True)
class FileUploadConfig:
    instruction: str = ""
    artifact_key: str = ""
    use_agent: str = ""

    @classmethod
    def from_node(cls, node: Any) -> "FileUploadConfig":
        return parse_config(FILE_UPLOAD_SPEC, cls, node)
