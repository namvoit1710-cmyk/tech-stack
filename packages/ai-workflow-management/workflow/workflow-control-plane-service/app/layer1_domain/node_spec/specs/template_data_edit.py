"""TEMPLATE_DATA_EDIT spec — governance/search-help-backed data editor.

Like ``DATA_EDIT`` (pauses the run for the user to review/edit a table and
emits an output ``file_id``), but the table's columns are driven by a
governance *template* and value-help columns fetch their options live from
Integration Hub search-help. See ``docs/template-data-editor-node.md``.
"""

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.node_spec.model import (
    Choice,
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

TEMPLATE_DATA_EDIT_SPEC = register(NodeSpec(
    node_type=NodeType.TEMPLATE_DATA_EDIT,
    name="Template Data Editor",
    description=(
        "Generates an editable data-entry table from a governance template, "
        "with value-help columns backed by Integration Hub search-help."
    ),
    category="human_in_the_loop.data",
    runtime="pending",
    node_class=NodeClass.BUSINESS,
    icon="TableProperties",
    color="#10B981",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    config_fields=(
        ConfigField(
            key="template_id", type=FieldType.STRING, label="Template ID",
            placeholder="{{$nodeA.template_id}}", required=True, expression=True,
            description="Governance template whose fields drive the table.",
        ),
        ConfigField(
            key="business_data_model_id", type=FieldType.STRING,
            label="Business Data Model ID", required=True, expression=True,
            placeholder="{{$nodeA.model_id}}",
            description="BDM used to build search-help endpoints.",
        ),
        ConfigField(
            key="requester_file_id", type=FieldType.STRING, label="Requester File",
            placeholder="{{$nodeA.__file_id}}", required=True, expression=True,
            description="File whose rows seed the editable table.",
        ),
        ConfigField(
            key="artifact_key", type=FieldType.STRING, label="Artifact Key",
            placeholder="edited_data",
            description=(
                "Optional name for the produced output artifact so "
                "$artifacts.<artifact_key> resolves to the edited rows."
            ),
        ),
        ConfigField(
            key="instruction", type=FieldType.STRING, label="Instruction",
            widget=Widget.TEXTAREA,
            placeholder="Review and complete the data before proceeding.",
        ),
        ConfigField(
            key="page_size", type=FieldType.NUMBER, label="Search-Help Page Size",
            default=100,
        ),
        ConfigField(
            key="readonly", type=FieldType.BOOLEAN, label="Read Only",
        ),
        ConfigField(
            key="data_mode", type=FieldType.STRING, label="Data Mode",
            choices=(Choice("csv_single", "Single Row"),
                     Choice("csv_multi", "Multiple Rows")),
            default="csv_multi",
        ),
    ),
    output_fields=(
        OutputField("file_id", FieldType.STRING, "File ID"),
        OutputField("data_mode", FieldType.STRING, "Data Mode"),
    ),
))


@dataclass(frozen=True)
class TemplateDataEditConfig:
    template_id: str = ""
    business_data_model_id: str = ""
    requester_file_id: str = ""
    artifact_key: str = ""
    instruction: str = ""
    page_size: int = 100
    readonly: bool = False
    data_mode: str = "csv_multi"

    @classmethod
    def from_node(cls, node: Any) -> "TemplateDataEditConfig":
        return parse_config(TEMPLATE_DATA_EDIT_SPEC, cls, node)
