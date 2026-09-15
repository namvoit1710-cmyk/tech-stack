"""BusinessNodeProvider — exposes tenant-authored NodeDefinition records."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.node_spec import NodeSpec, get_spec
from app.layer1_domain.value_objects.node_kind import MINE_COLLECTION, NodeKind, safe_kind_value
from app.layer2_application.definition.node_definition_resolver import (
    NodeDefinitionResolver,
    ResolvedNodeDefinition,
)
from app.layer2_application.repositories.node_definition_repository_interface import (
    INodeDefinitionRepository,
)
from app.layer2_application.contracts import (
    FormField,
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodePort,
    NodePorts,
    NodeRuntime,
    NodeVisibility,
)


logger = logging.getLogger(__name__)


def _business_id(nd_id: str) -> str:
    return f"business:{nd_id}"


def _runtime_for_base_type(base_type_value: str) -> NodeRuntime:
    """Business nodes inherit runtime from their base_type."""
    if base_type_value in ("INPUT", "HUMAN_ACTION", "DATA_EDIT", "FILE_UPLOAD"):
        return NodeRuntime.PENDING
    if base_type_value == "TASK":
        return NodeRuntime.WORKER
    return NodeRuntime.ENGINE


def _ports_from_dict(ports: Any, base: NodeSpec) -> NodePorts:
    """Serialise the definition's declared ports, falling back to the base-type
    spec's ports when it declares none.

    A definition that leaves ``ports`` empty (e.g. gateway-generated nodes, which
    persist ``ports={}``) inherits its base type's ports. This mirrors the runtime
    ``node_spec_resolver._ports_from_dict`` so the editor catalog and the engine
    agree on a node's handles instead of the catalog rendering it port-less.
    """
    if not isinstance(ports, dict) or not (ports.get("in") or ports.get("out")):
        return NodePorts(
            **{
                "in": [NodePort(id=p.id, label=p.label) for p in base.in_ports],
                "out": [NodePort(id=p.id, label=p.label) for p in base.out_ports],
            }
        )
    return NodePorts(
        **{
            "in": [NodePort(id=p.get("id", ""), label=p.get("label", "")) for p in ports.get("in", [])],
            "out": [NodePort(id=p.get("id", ""), label=p.get("label", "")) for p in ports.get("out", [])],
        }
    )


def _base_type_value(nd: NodeDefinition) -> str:
    return nd.base_type.value if hasattr(nd.base_type, "value") else str(nd.base_type)


def _sanitize_collection(collection: str) -> str:
    """A business collection is user-named; strip any '.' so it can't corrupt the
    dotted <collection>.<kind> category path. Blank -> the default bucket."""
    cleaned = (collection or "").replace(".", " ").strip()
    return cleaned or MINE_COLLECTION


def _build_summary(nd: NodeDefinition) -> NodeDefinitionSummary:
    collection = _sanitize_collection(nd.collection)
    # `nd.kind` is EITHER a plain str (an explicit value the client set) OR a real
    # NodeKind (a blank that __post_init__ healed via default_kind). Two traps, both
    # hit live:
    #   1. f"{NodeKind.ACTION}" renders "NodeKind.ACTION", not "action" — that shipped
    #      once and produced category="My Nodes.NodeKind.ACTION".
    #   2. NodeDefinitionSummary.kind is a pydantic NodeKind field, so an unknown value
    #      RAISES — while assembling the catalog. Blast radius is not one node: it is
    #      /api/v2/node-catalog 500ing and every tenant getting an empty palette.
    # safe_kind_value closes both: always a plain str, never raises, unknown -> action
    # with a warning. Create/update still reject an invalid kind with a clean
    # VALIDATION_ERROR — this is the READ path, and a reader must be forgiving.
    kind = safe_kind_value(nd.kind, context=f"node_definition_id={nd.id!r}")
    return NodeDefinitionSummary(
        id=_business_id(nd.id),
        name=nd.name,
        description=nd.description,
        provider="business",
        node_type=_base_type_value(nd),
        category=f"{collection}.{kind}",
        kind=kind,
        collection=collection,
        icon=nd.icon or "Cog",
        color=nd.color or "#3B82F6",
        tags=list(nd.tags),
        spec_version=nd.spec_version or "1.0.0",
        visibility=NodeVisibility.VISIBLE,
        runtime=_runtime_for_base_type(_base_type_value(nd)),
        ports=_ports_from_dict(nd.ports, get_spec(nd.base_type)),
    )


def _form_fields(schema: Any, nd_id: str, kind: str, strict: bool) -> list[FormField]:
    if not isinstance(schema, list):
        return []
    if strict:
        return [FormField.model_validate(f) for f in schema]
    out: list[FormField] = []
    for f in schema:
        try:
            out.append(FormField.model_validate(f))
        except Exception as exc:
            logger.warning(
                "business node %r has malformed %s_schema field; skipping. err=%s",
                nd_id, kind, exc,
            )
    return out


def _build_detail(
    nd: NodeDefinition,
    resolved: ResolvedNodeDefinition,
    strict: bool,
) -> NodeDefinitionDetail:
    summary = _build_summary(nd)
    # Merged schemas from the resolved chain take precedence; fall back to
    # the leaf record for fields the resolver doesn't flatten.
    input_schema = _form_fields(resolved.input_schema or nd.input_schema, nd.id, "input", strict)
    output_schema = _form_fields(resolved.output_schema or nd.output_schema, nd.id, "output", strict)
    return NodeDefinitionDetail.model_validate({
        **summary.model_dump(by_alias=True),
        "inputSchema": [f.model_dump(by_alias=True) for f in input_schema],
        "outputSchema": [f.model_dump(by_alias=True) for f in output_schema],
        "capabilities": [],
        "baseWorkerId": resolved.base_worker_id,
        "baseNodeType": _base_type_value(nd),
        "parentDefinitionId": nd.parent_definition_id,
        "lockedDefaults": dict(resolved.locked_defaults),
        "inputMapping": dict(resolved.input_mapping),
        "outputMapping": dict(resolved.output_mapping),
        "dataMode": resolved.data_mode,
        "editMode": resolved.edit_mode,
    })


class BusinessNodeProvider:
    """Exposes tenant-authored business NodeDefinition records.

    System-tenant definitions are visible to every tenant. For non-system
    tenants, tenant-local definitions are merged on top (tenant wins on id
    collision).
    """

    def __init__(
        self,
        repo: INodeDefinitionRepository,
        resolver: NodeDefinitionResolver,
        strict_schema_validation: bool = False,
    ) -> None:
        self._repo = repo
        self._resolver = resolver
        self._strict = strict_schema_validation

    def list_summaries(self, tenant_id: str = "system") -> list[NodeDefinitionSummary]:
        by_id: dict[str, NodeDefinition] = {
            nd.id: nd for nd in self._repo.list_by_tenant("system")
        }
        if tenant_id and tenant_id != "system":
            for nd in self._repo.list_by_tenant(tenant_id):
                by_id[nd.id] = nd
        return [_build_summary(nd) for nd in by_id.values()]

    def get_detail(
        self,
        node_id: str,
        spec_version: Optional[str] = None,
    ) -> Optional[NodeDefinitionDetail]:
        if not node_id.startswith("business:"):
            return None
        def_id = node_id.removeprefix("business:")
        if not def_id:
            return None
        nd = self._repo.get_by_id(def_id)
        if nd is None:
            return None
        # Resolver raises ValueError on spec_version mismatch; let it propagate.
        resolved = self._resolver.resolve(def_id, self._repo, spec_version=spec_version)
        return _build_detail(nd, resolved, self._strict)
