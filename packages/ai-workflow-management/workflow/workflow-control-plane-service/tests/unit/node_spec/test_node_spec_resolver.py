"""NodeSpecResolver — runtime-built specs for worker/business nodes."""

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.node_spec import FieldType, get_spec
from app.layer1_domain.node_spec.validation import validate_bindings
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.definition.node_spec_resolver import (
    NodeSpecResolver,
    spec_from_autoform,
    spec_from_node_definition,
    spec_from_worker,
)

_WORKER = {
    "worker_type": "http-request",
    "name": "HTTP Request",
    "description": "Calls an HTTP endpoint.",
    "spec_version": "2.1.0",
    "icon": "Globe",
    "color": "#123456",
    "ports": {
        "in": [{"id": "in_default", "label": "Input"}],
        "out": [{"id": "success", "label": "OK"}, {"id": "failure", "label": "Err"}],
    },
    "input_schema": [
        {"key": "url", "outputType": "string",
         "fieldConfig": {"wrapperProps": {"label": "URL", "required": True},
                         "rules": [{"method": "required"}],
                         "controlProps": {"placeholder": "https://"}}},
        {"key": "retries", "outputType": "number",
         "fieldConfig": {"wrapperProps": {"label": "Retries"}}},
        {"key": "nested", "outputType": "object",
         "fieldConfig": {}, "fields": [{"key": "child"}]},
    ],
    "output_schema": [
        {"key": "status_code", "outputType": "number",
         "fieldConfig": {"wrapperProps": {"label": "Status"}}},
    ],
}


class _WorkerClient:
    def __init__(self, worker=None):
        self._worker = worker

    def get_worker_by_type(self, worker_type):
        return self._worker


class _DefnRepo:
    def __init__(self, defn=None):
        self._defn = defn

    def get_by_id(self, defn_id):
        return self._defn


def test_spec_from_autoform_simple_subset() -> None:
    fields = spec_from_autoform(_WORKER["input_schema"])
    by_key = {f.key: f for f in fields}
    assert by_key["url"].required is True
    assert by_key["url"].type == FieldType.STRING
    assert by_key["url"].placeholder == "https://"
    assert by_key["retries"].type == FieldType.NUMBER
    # nested containers become opaque objects
    assert by_key["nested"].type == FieldType.OBJECT


def test_spec_from_worker_builds_task_runtime_spec() -> None:
    spec = spec_from_worker(_WORKER)
    assert spec.runtime == "worker"
    assert spec.node_type == NodeType.TASK
    assert spec.config_keys == frozenset({"input_types"})  # TASK config only
    assert {f.key for f in spec.input_fields} == {"url", "retries", "nested"}
    assert [p.id for p in spec.out_ports] == ["success", "failure"]
    assert spec.spec_version == "2.1.0"
    assert spec.icon == "Globe"


def test_spec_from_node_definition_overlays_base() -> None:
    defn = NodeDefinition(
        id="nd-1", tenant_id="t1", name="Get Orders",
        base_type=NodeType.TASK, base_worker_id="http-request",
        input_schema=[{
            "key": "order_id", "outputType": "string",
            "fieldConfig": {"wrapperProps": {"label": "Order"}},
        }],
        icon="Package",
    )
    spec = spec_from_node_definition(defn)
    assert spec.name == "Get Orders"
    assert spec.icon == "Package"
    assert {f.key for f in spec.input_fields} == {"order_id"}
    assert spec.config_keys == frozenset({"input_types"})


def test_resolver_builtin_path() -> None:
    node = WorkflowNode(workflow_id="n1", node_type=NodeType.MERGE)
    spec = NodeSpecResolver().resolve(node)
    assert spec is get_spec(NodeType.MERGE)


def test_resolver_worker_path_and_outage_degrades() -> None:
    node = WorkflowNode(
        workflow_id="n1", node_type=NodeType.TASK, worker_type="http-request",
    )
    spec = NodeSpecResolver(
        worker_registry_client=_WorkerClient(_WORKER),
    ).resolve(node)
    assert {f.key for f in spec.input_fields} == {"url", "retries", "nested"}

    class _Broken:
        def get_worker_by_type(self, worker_type):
            raise RuntimeError("registry down")

    degraded = NodeSpecResolver(worker_registry_client=_Broken()).resolve(node)
    assert degraded is get_spec(NodeType.TASK)


def test_resolver_definition_takes_precedence_over_worker() -> None:
    defn = NodeDefinition(
        id="nd-1", tenant_id="t1", name="Tpl",
        base_type=NodeType.TASK, base_worker_id="http-request",
    )
    node = WorkflowNode(
        workflow_id="n1", node_type=NodeType.TASK,
        worker_type="http-request", node_definition_id="nd-1",
    )
    spec = NodeSpecResolver(
        node_definition_repo=_DefnRepo(defn),
        worker_registry_client=_WorkerClient(_WORKER),
    ).resolve(node)
    assert spec.name == "Tpl"


def test_unknown_binding_keys_warn_not_error() -> None:
    spec = spec_from_worker(_WORKER)
    node = WorkflowNode(
        workflow_id="n1", node_type=NodeType.TASK, worker_type="http-request",
        input_binding={"url": "https://x", "typo_key": 1, "_internal": 2},
    )
    violations = validate_bindings(spec, node)
    assert [(v.key, v.code, v.severity) for v in violations] == [
        ("typo_key", "unknown_binding", "warn"),
    ]


def test_no_declared_inputs_means_no_binding_checks() -> None:
    node = WorkflowNode(
        workflow_id="n1", node_type=NodeType.TASK, worker_type="w",
        input_binding={"anything": "goes"},
    )
    assert validate_bindings(get_spec(NodeType.TASK), node) == []
