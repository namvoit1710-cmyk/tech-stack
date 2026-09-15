"""Return the complete expression syntax reference for frontend integration."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GetExpressionReferenceCommand:
    pass


@dataclass
class ExpressionSource:
    name: str = ""
    description: str = ""
    examples: list[str] = field(default_factory=list)
    available: str = ""
    fields: list[dict[str, str]] = field(default_factory=list)


@dataclass
class MetaPath:
    prefix: str = ""
    description: str = ""
    example: str = ""
    returns: str = "string"


@dataclass
class Operator:
    op: str = ""
    description: str = ""
    example: str = ""


@dataclass
class TransformDef:
    name: str = ""
    description: str = ""
    params: list[str] = field(default_factory=list)
    example_input: Any = None
    example_spec: dict[str, Any] = field(default_factory=dict)
    example_output: Any = None


@dataclass
class GetExpressionReferenceResult:
    syntax: str = ""
    sources: list[ExpressionSource] = field(default_factory=list)
    meta_paths: list[MetaPath] = field(default_factory=list)
    operators: list[Operator] = field(default_factory=list)
    logical_operators: list[str] = field(default_factory=list)
    path_syntax: dict[str, str] = field(default_factory=dict)
    transforms: list[TransformDef] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


_SOURCES = [
    ExpressionSource(
        name="input",
        description="Workflow input data passed at run start.",
        examples=["{{$input.customer_name}}", "{{$input.items[0].id}}"],
        available="always",
    ),
    ExpressionSource(
        name="workflow",
        description="Metadata about the current workflow definition.",
        examples=["{{$workflow.id}}", "{{$workflow.name}}", "{{$workflow.version}}"],
        available="always",
        fields=[
            {"name": "id", "type": "string", "description": "Workflow ID"},
            {"name": "name", "type": "string", "description": "Workflow display name"},
            {"name": "version", "type": "string", "description": "Workflow version"},
            {"name": "description", "type": "string", "description": "Workflow description"},
        ],
    ),
    ExpressionSource(
        name="run",
        description="Metadata about the current workflow run.",
        examples=["{{$run.id}}", "{{$run.tenant_id}}", "{{$run.correlation_id}}"],
        available="always",
        fields=[
            {"name": "id", "type": "string", "description": "Run ID"},
            {"name": "tenant_id", "type": "string", "description": "Tenant ID"},
            {"name": "correlation_id", "type": "string", "description": "Correlation ID"},
            {"name": "started_at", "type": "string", "description": "Run start timestamp (ISO 8601)"},
        ],
    ),
    ExpressionSource(
        name="variables",
        description="Mutable run state written by SET_VARIABLE nodes, readable by downstream nodes.",
        examples=["{{$variables.user_count}}", "{{$variables.is_ready}}"],
        available="after an upstream SET_VARIABLE node sets the name",
    ),
    ExpressionSource(
        name="artifacts",
        description="Global named artifacts produced by completed nodes during the run.",
        examples=["{{$artifacts.uploaded_data}}", "{{$artifacts.report_csv}}"],
        available="after a node produces a named artifact",
    ),
    ExpressionSource(
        name="<node_id> or <node_name>",
        description="Output of a completed node, referenced by UUID or display name.",
        examples=[
            "{{$node_abc.status_code}}",
            "{{$HTTP_Request.response_body}}",
            "{{$Extract_Data.columns[0]}}",
        ],
        available="after node completes",
    ),
    ExpressionSource(
        name="trigger",
        description="Trigger event data set by a TRIGGER entry node.",
        examples=["{{$trigger.type}}", "{{$trigger.body.event_id}}"],
        available="when workflow has a TRIGGER entry node",
        fields=[
            {"name": "type", "type": "string", "description": "Trigger type"},
            {"name": "body", "type": "object", "description": "Trigger payload"},
        ],
    ),
    ExpressionSource(
        name="loop",
        description="Current loop iteration context inside a LOOP body.",
        examples=["{{$loop.item}}", "{{$loop.index}}", "{{$loop.total}}"],
        available="inside LOOP body nodes",
        fields=[
            {"name": "item", "type": "any", "description": "Current iteration item"},
            {"name": "index", "type": "number", "description": "Current iteration index (0-based)"},
            {"name": "items", "type": "array", "description": "Full items array"},
            {"name": "total", "type": "number", "description": "Total number of iterations"},
        ],
    ),
    ExpressionSource(
        name="custom",
        description="User-provided resolved input before locked_defaults are applied. Transient — only available inside node definition input_mapping.",
        examples=["{{$custom.user_url}}", "{{$custom.selected_table}}"],
        available="only in node definition input_mapping",
    ),
    ExpressionSource(
        name="_output",
        description="Raw worker output data. Transient — only available inside output_mapping and output_binding.",
        examples=["{{$_output.response_body}}", "{{$_output.metadata.status_code}}"],
        available="only in output_mapping and output_binding",
    ),
]

_META_PATHS = [
    MetaPath(
        prefix="__file_id",
        description="Primary CSV file_id of a file-backed node output.",
        example="{{$node_A.__file_id}}",
        returns="string",
    ),
    MetaPath(
        prefix="__artifacts",
        description="Array of artifacts attached to the node output. Supports index access.",
        example="{{$node_A.__artifacts[0].file_id}}",
        returns="array",
    ),
    MetaPath(
        prefix="__artifact.<filename>",
        description="Lookup an artifact's file_id by its filename.",
        example="{{$node_A.__artifact.export.csv}}",
        returns="string (file_id)",
    ),
    MetaPath(
        prefix="__content_type",
        description="Content type of the primary file.",
        example="{{$node_A.__content_type}}",
        returns="string",
    ),
    MetaPath(
        prefix="__row_count",
        description="Number of rows in the primary CSV.",
        example="{{$node_A.__row_count}}",
        returns="number",
    ),
    MetaPath(
        prefix="__columns",
        description="Column names of the primary CSV. Supports index access.",
        example="{{$node_A.__columns[0]}}",
        returns="array",
    ),
]

_OPERATORS = [
    Operator(op="==", description="Equal", example="{{$node.status_code}} == 200"),
    Operator(op="!=", description="Not equal", example="{{$node.status}} != 'error'"),
    Operator(op=">", description="Greater than", example="{{$node.count}} > 10"),
    Operator(op=">=", description="Greater or equal", example="{{$node.count}} >= 10"),
    Operator(op="<", description="Less than", example="{{$node.count}} < 100"),
    Operator(op="<=", description="Less or equal", example="{{$node.count}} <= 100"),
    Operator(op="IN", description="Left value contained in right", example="{{$node.status}} IN 'active,pending'"),
    Operator(op="CONTAINS", description="Left contains right", example="{{$node.name}} CONTAINS 'test'"),
]

_PATH_SYNTAX = {
    "dot_access": "{{$source.key}}",
    "array_index": "{{$source.items[0]}}",
    "nested": "{{$source.parent.child.value}}",
    "mixed": "{{$source.items[0].name}}",
}

_TRANSFORMS = [
    TransformDef(
        name="key_value_pairs",
        description="Convert array of {key, value} objects to a flat dict.",
        params=["source", "key_field", "value_field"],
        example_input=[{"header_name": "Content-Type", "header_value": "application/json"}],
        example_spec={
            "_transform": "key_value_pairs",
            "source": "{{$custom.headers}}",
            "key_field": "header_name",
            "value_field": "header_value",
        },
        example_output={"Content-Type": "application/json"},
    ),
    TransformDef(
        name="pluck",
        description="Extract a single field from each object in an array.",
        params=["source", "field"],
        example_input=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
        example_spec={"_transform": "pluck", "source": "{{$custom.users}}", "field": "name"},
        example_output=["Alice", "Bob"],
    ),
    TransformDef(
        name="join",
        description="Join array elements into a single string.",
        params=["source", "separator"],
        example_input=["a", "b", "c"],
        example_spec={"_transform": "join", "source": "{{$custom.tags}}", "separator": ","},
        example_output="a,b,c",
    ),
    TransformDef(
        name="first",
        description="Return the first element of an array.",
        params=["source"],
        example_input=[{"id": 1}, {"id": 2}],
        example_spec={"_transform": "first", "source": "{{$custom.items}}"},
        example_output={"id": 1},
    ),
    TransformDef(
        name="flat_map",
        description="Extract a single field from each object (alias for pluck).",
        params=["source", "field"],
        example_input=[{"id": 1}, {"id": 2}],
        example_spec={"_transform": "flat_map", "source": "{{$custom.items}}", "field": "id"},
        example_output=[1, 2],
    ),
]


class GetExpressionReferenceUseCase:
    """Return the static expression syntax reference catalog."""

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def execute(self, command: GetExpressionReferenceCommand) -> GetExpressionReferenceResult:
        return GetExpressionReferenceResult(
            syntax="{{$<source>.<path>}}",
            sources=list(_SOURCES),
            meta_paths=list(_META_PATHS),
            operators=list(_OPERATORS),
            logical_operators=["AND", "OR"],
            path_syntax=dict(_PATH_SYNTAX),
            transforms=list(_TRANSFORMS),
        )
