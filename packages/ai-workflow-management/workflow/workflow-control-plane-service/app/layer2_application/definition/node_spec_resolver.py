"""NodeSpecResolver — one spec per node, builtin or runtime-built.

Builtins resolve to the static specs in ``app.layer1_domain.node_spec``;
worker- and definition-backed nodes get a spec built on the fly from
registry/repository data (NodeSpec Phase 5, design §7):

* ``node_definition_id`` → base-type spec overlaid with the definition's
  ``input_schema`` as InputFields plus its ports/icon/color.
  ``locked_defaults`` are NOT spec fields — they live in
  ``WorkflowNode.definition_snapshot`` and apply at dispatch.
* ``worker_type`` → the minimal TASK config plus the worker's registered
  ``input_schema``/``output_schema`` as Input/Output fields.

Resolution failures (repo not wired, lookup error, malformed schema)
degrade to the builtin spec for the node's type — save-time validation
must never explode because the worker registry is down.

Note on serving vs validating: the catalog providers serve worker /
definition AutoForm schemas verbatim (rendering a parsed spec would be
lossy for arbitrary worker forms). The specs built here exist for
validation and introspection; ``spec_from_autoform`` parses only the
flat simple subset and passes anything nested through as an opaque
OBJECT input field.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.node_spec import (
    FieldType,
    InputField,
    NodeSpec,
    OutputField,
    get_spec,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port

logger = logging.getLogger(__name__)

_AUTOFORM_TYPE_MAP = {
    "string": FieldType.STRING,
    "number": FieldType.NUMBER,
    "boolean": FieldType.BOOLEAN,
    "object": FieldType.OBJECT,
    "array": FieldType.ARRAY,
}


def spec_from_autoform(fields: Any) -> tuple[InputField, ...]:
    """Parse AutoForm field dicts into InputFields (flat simple subset).

    Container/nested fields become opaque OBJECT entries — enough for
    key-level validation without pretending to model arbitrary forms.
    """
    if not isinstance(fields, (list, tuple)):
        return ()
    out: list[InputField] = []
    for f in fields:
        if not isinstance(f, dict) or not f.get("key"):
            continue
        cfg = f.get("fieldConfig") or {}
        wrapper_props = cfg.get("wrapperProps") or {}
        rules = cfg.get("rules") or []
        required = bool(wrapper_props.get("required")) or any(
            isinstance(r, dict) and r.get("method") == "required" for r in rules
        )
        field_type = _AUTOFORM_TYPE_MAP.get(
            str(f.get("outputType", "string")).lower(), FieldType.OBJECT,
        )
        if f.get("fields"):  # nested container → opaque
            field_type = FieldType.OBJECT
        out.append(InputField(
            key=str(f["key"]),
            type=field_type,
            label=str(wrapper_props.get("label", "")),
            required=required,
            placeholder=str((cfg.get("controlProps") or {}).get("placeholder", "")),
        ))
    return tuple(out)


def _output_fields_from_autoform(fields: Any) -> tuple[OutputField, ...]:
    if not isinstance(fields, (list, tuple)):
        return ()
    out: list[OutputField] = []
    for f in fields:
        if not isinstance(f, dict) or not f.get("key"):
            continue
        wrapper_props = (f.get("fieldConfig") or {}).get("wrapperProps") or {}
        out.append(OutputField(
            key=str(f["key"]),
            type=_AUTOFORM_TYPE_MAP.get(
                str(f.get("outputType", "string")).lower(), FieldType.OBJECT,
            ),
            label=str(wrapper_props.get("label", "")),
        ))
    return tuple(out)


def _ports_from_dict(ports: Any, base: NodeSpec) -> tuple[tuple[Port, ...], tuple[Port, ...]]:
    if not isinstance(ports, dict) or not (ports.get("in") or ports.get("out")):
        return base.in_ports, base.out_ports
    def _parse(items: Any) -> tuple[Port, ...]:
        return tuple(
            Port(id=p.get("id", ""), label=p.get("label", ""))
            for p in (items or []) if isinstance(p, dict)
        )
    return _parse(ports.get("in")), _parse(ports.get("out"))


def spec_from_worker(worker: dict[str, Any]) -> NodeSpec:
    """Runtime spec for a registry-registered worker (TASK runtime)."""
    base = get_spec(NodeType.TASK)
    in_ports, out_ports = _ports_from_dict(worker.get("ports"), base)
    return NodeSpec(
        node_type=base.node_type,
        name=worker.get("name") or worker.get("worker_type", "Task"),
        description=worker.get("description", ""),
        category=worker.get("category") or "worker.integration",
        runtime="worker",
        node_class=base.node_class,
        icon=worker.get("icon") or base.icon,
        color=worker.get("color") or base.color,
        in_ports=in_ports,
        out_ports=out_ports,
        config_fields=base.config_fields,  # TASK config = input_types only
        input_fields=spec_from_autoform(worker.get("input_schema")),
        output_fields=_output_fields_from_autoform(worker.get("output_schema")),
        spec_version=worker.get("spec_version") or "1.0.0",
    )


def spec_from_node_definition(defn: NodeDefinition) -> NodeSpec:
    """Runtime spec for a business-node template (NodeDefinition).

    Base = the definition's ``base_type`` builtin spec, overlaid with
    the definition's declared input/output schema, ports, and palette
    metadata. ``locked_defaults``/mappings stay in
    ``definition_snapshot`` (dispatch-time concern, not spec).
    """
    base = get_spec(defn.base_type)
    in_ports, out_ports = _ports_from_dict(defn.ports, base)
    return NodeSpec(
        node_type=defn.base_type,
        name=defn.name or base.name,
        description=defn.description or base.description,
        category="business",
        runtime=base.runtime,
        node_class=base.node_class,
        icon=defn.icon or base.icon,
        color=defn.color or base.color,
        in_ports=in_ports,
        out_ports=out_ports,
        config_fields=base.config_fields,
        input_fields=spec_from_autoform(defn.input_schema),
        output_fields=(
            _output_fields_from_autoform(defn.output_schema)
            or base.output_fields
        ),
        spec_version=defn.spec_version or "1.0.0",
    )


class NodeSpecResolver:
    """Resolve the effective NodeSpec for a workflow node."""

    def __init__(
        self,
        node_definition_repo: Any = None,
        worker_registry_client: Any = None,
    ) -> None:
        self._definition_repo = node_definition_repo
        self._worker_client = worker_registry_client

    def resolve(self, node: WorkflowNode) -> NodeSpec:
        builtin = get_spec(node.node_type)
        try:
            if node.node_definition_id and self._definition_repo is not None:
                defn = self._definition_repo.get_by_id(node.node_definition_id)
                if defn is not None:
                    return spec_from_node_definition(defn)
            if node.worker_type and self._worker_client is not None:
                worker = self._worker_client.get_worker_by_type(node.worker_type)
                if worker:
                    return spec_from_worker(worker)
        except Exception as exc:  # registry outage must not block validation
            logger.warning(
                "node spec resolution degraded to builtin for node %s: %s",
                node.id, exc,
            )
        return builtin


__all__ = [
    "NodeSpecResolver",
    "spec_from_autoform",
    "spec_from_node_definition",
    "spec_from_worker",
]
