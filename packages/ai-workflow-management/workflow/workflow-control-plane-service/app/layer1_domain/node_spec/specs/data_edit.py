"""DATA_EDIT spec — pause to review/edit upstream artifact data.

The form is tab-grouped (Configuration / Settings) but the config keys
are flat — tabs are pure presentation (``tab=`` hints + ``form_tabs``).
``columns`` is declared for the first time: the handler read it
(`data_edit_handler.py:256`) while no catalog mentioned it.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from app.layer1_domain.node_spec.model import (
    Choice,
    ConfigField,
    FieldType,
    FormGroup,
    NodeSpec,
    OutputField,
    Widget,
    parse_config,
)
from app.layer1_domain.node_spec.registry import register
from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

_FULL_SPAN = "xl-12 lg-12 md-12 xs-12"

DATA_EDIT_SPEC = register(NodeSpec(
    node_type=NodeType.DATA_EDIT,
    name="Data Edit",
    description="Pauses workflow to let a user review and edit upstream artifact data.",
    category="human_in_the_loop.data",
    runtime="pending",
    node_class=NodeClass.BUSINESS,
    icon="TableProperties",
    color="#10B981",
    in_ports=(Port(id="in_default", label="Input"),),
    out_ports=(Port(id="out_default", label="Output"),),
    form_tabs=(
        FormGroup(key="main", label="Configuration"),
        FormGroup(key="settings_tab", label="Settings"),
    ),
    form_tabs_key="data_edit_tabs",
    config_fields=(
        ConfigField(
            key="file_id", type=FieldType.STRING, label="Source Data",
            placeholder="{{$nodeA.__file_id}}", required=True,
            expression=True, tab="main",
        ),
        ConfigField(
            key="artifact_key", type=FieldType.STRING, label="Artifact Key",
            placeholder="uploaded_file", tab="main",
        ),
        ConfigField(
            key="instruction", type=FieldType.STRING, label="Instruction",
            widget=Widget.TEXTAREA, tab="main",
            placeholder="Review and edit the data before proceeding.",
        ),
        ConfigField(
            key="data_mode", type=FieldType.STRING, label="Data Mode",
            choices=(Choice("csv_single", "Single Row"),
                     Choice("csv_multi", "Multiple Rows")),
            tab="main",
        ),
        ConfigField(
            key="query", type=FieldType.STRING, label="OData Query",
            placeholder="$filter=qty gt 5&$top=10", tab="main",
        ),
        ConfigField(
            key="actions", type=FieldType.JSON, label="Actions (JSON array)",
            widget=Widget.OBJECT_EDITOR, tab="settings_tab",
            render_props={"labelSpan": _FULL_SPAN, "fieldSpan": _FULL_SPAN},
        ),
        ConfigField(
            key="settings", type=FieldType.JSON,
            label="Extra Settings (JSON array)",
            widget=Widget.OBJECT_EDITOR, tab="settings_tab",
            render_props={"labelSpan": _FULL_SPAN, "fieldSpan": _FULL_SPAN},
        ),
        ConfigField(
            key="readonly", type=FieldType.BOOLEAN, label="Read Only",
            tab="settings_tab",
        ),
        ConfigField(
            key="custom_data_update", type=FieldType.BOOLEAN,
            label="Custom data update", tab="settings_tab",
        ),
        # SA-1239: the designer picks the row-selection mode for the approval
        # "Input Requested" table from a dropdown. single → radio-select row,
        # multiple → checkbox-select rows, none → no row selection at all
        # (wrapperProps carries no selectionMode). Stored on
        # node.config.selectionMode; the dispatch + runtime schema builders
        # forward it (and drop it for "none"). A plain choices field renders as
        # a SelectControl (like data_mode). Key is intentionally camelCase
        # (unlike the snake_case siblings) — it passes straight through to the
        # FE's wrapperProps.selectionMode prop, so renaming it would break that.
        ConfigField(
            key="selectionMode", type=FieldType.STRING, label="Select Mode",
            choices=(Choice("none", "None"),
                     Choice("single", "Single"),
                     Choice("multiple", "Multiple")),
            tab="settings_tab",
        ),
        # Engine-consumed.
        ConfigField(key="columns", type=FieldType.ARRAY, label="Seed Columns",
                    internal=True),
        ConfigField(key="input_types", type=FieldType.OBJECT,
                    label="Input Types", internal=True),
    ),
    output_fields=(
        OutputField("file_id", FieldType.STRING, "File ID"),
        OutputField("data_mode", FieldType.STRING, "Data Mode"),
    ),
))


@dataclass(frozen=True)
class DataEditConfig:
    file_id: str = ""
    artifact_key: str = ""
    instruction: str = ""
    data_mode: str = ""
    query: str = ""
    actions: Any = None
    settings: Any = None
    readonly: bool = False
    custom_data_update: bool = False
    selectionMode: str = ""  # SA-1239: "none" | "single" | "multiple" ("" = unset → default)
    columns: tuple[str, ...] = ()
    input_types: Optional[Mapping[str, str]] = None

    @classmethod
    def from_node(cls, node: Any) -> "DataEditConfig":
        return parse_config(DATA_EDIT_SPEC, cls, node)
