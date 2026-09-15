"""BuiltinNodeProvider — serves the builtin node types from their specs.

NodeSpec Phase 5: every per-type map this module used to carry
(runtime, category, name, description, ports) now lives on the static
``NodeSpec`` and the form schemas are rendered from it — one source of
truth, no drift.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.layer1_domain.node_spec import BUILTIN_SPECS, NodeSpec
from app.layer1_domain.node_spec.form_renderer import (
    render_input_schema,
    render_output_schema,
)
from app.layer1_domain.value_objects.node_kind import CORE_COLLECTION, NodeKind, kind_for_node_type
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.contracts import (
    FormField,
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodePort,
    NodePorts,
    NodeRuntime,
    NodeVisibility,
)

_log = logging.getLogger(__name__)

_BUILTIN_SPEC_VERSION = "1.0.0"

# Catalog surface = the node types an author can drag from the palette.
# LOOP_EXIT/LOOP_CONTINUE/TASK have specs but no catalog presence (TASK
# is served per-worker by WorkerNodeProvider).
_UNLISTED = {NodeType.TASK, NodeType.LOOP_EXIT, NodeType.LOOP_CONTINUE}


def _catalog_specs() -> list[NodeSpec]:
    return [
        spec for node_type, spec in BUILTIN_SPECS.items()
        if node_type not in _UNLISTED
    ]


def _ports_from_spec(spec: NodeSpec) -> NodePorts:
    return NodePorts(
        **{
            "in": [NodePort(id=p.id, label=p.label) for p in spec.in_ports],
            "out": [NodePort(id=p.id, label=p.label) for p in spec.out_ports],
        }
    )


def _builtin_id(node_type: NodeType) -> str:
    return f"builtin:{node_type.value.lower()}"


# SA-1737/FIX2: derive a functional kind from the node's TYPE (not its category
# string) so engine primitives join the (kind, collection) taxonomy under the
# single "Core" collection. The mapping — ``_KIND_BY_NODE_TYPE`` — lives in the
# domain (node_kind.py) as the ONE source of truth; a category-keyed map here
# used to disagree with the use-case layer's ``default_kind`` on 11 of 18
# catalog specs and silently fall back to "action" for any category it hadn't
# been told about (that's how the WORKFLOW/sub-workflow node lost its "logic"
# kind). Keyed by the NodeType enum via ``kind_for_node_type``, an unmapped spec
# is caught in CI by test_node_kind_totality — see below for why runtime degrades
# rather than raising.
def _builtin_kind(node_type: NodeType) -> str:
    kind = kind_for_node_type(node_type)
    if kind is None:
        # Gate the gap in CI, degrade in production — deliberately NOT a raise.
        # test_node_kind_totality asserts every catalog spec's node_type is mapped, so a
        # missing entry cannot merge. If one ever reaches runtime anyway, the blast radius
        # of raising here is the WHOLE catalog: list_summaries() dies, /api/v2/node-catalog
        # 500s, and every tenant gets an empty palette. The cost of falling back is one node
        # grouped under the wrong bucket. A cosmetic bug must never be paid for with an outage.
        _log.error(
            "builtin node type %r has no entry in _KIND_BY_NODE_TYPE "
            "(app/layer1_domain/value_objects/node_kind.py) — falling back to %r. "
            "This should be impossible: test_node_kind_totality gates it.",
            node_type, NodeKind.ACTION.value,
        )
        return NodeKind.ACTION.value
    return kind.value


def _build_summary(spec: NodeSpec) -> NodeDefinitionSummary:
    kind = _builtin_kind(spec.node_type)
    return NodeDefinitionSummary(
        id=_builtin_id(spec.node_type),
        name=spec.name,
        description=spec.description,
        provider="builtin",
        node_type=spec.node_type.value,
        category=f"{CORE_COLLECTION}.{kind}",
        kind=kind,
        collection=CORE_COLLECTION,
        icon=spec.icon,
        color=spec.color,
        tags=[],
        spec_version=_BUILTIN_SPEC_VERSION,
        visibility=NodeVisibility.VISIBLE,
        runtime=NodeRuntime(spec.runtime),
        ports=_ports_from_spec(spec),
    )


def _build_detail(spec: NodeSpec) -> NodeDefinitionDetail:
    summary = _build_summary(spec)
    input_schema = [
        FormField.model_validate(f) for f in render_input_schema(spec)
    ]
    output_schema = [
        FormField.model_validate(f) for f in render_output_schema(spec)
    ]
    return NodeDefinitionDetail.model_validate({
        **summary.model_dump(by_alias=True),
        "inputSchema": [f.model_dump(by_alias=True) for f in input_schema],
        "outputSchema": [f.model_dump(by_alias=True) for f in output_schema],
        "capabilities": [
            {"domain": spec.node_type.value.lower(), "action": "execute"},
        ],
    })


class BuiltinNodeProvider:
    """Serves the builtin node types from their static specs.

    ``spec_version`` is always ``"1.0.0"`` — builtins don't version
    independently. Callers requesting a different version get ``ValueError``.
    """

    def list_summaries(self, tenant_id: str = "system") -> list[NodeDefinitionSummary]:
        return [_build_summary(spec) for spec in _catalog_specs()]

    def get_detail(
        self,
        node_id: str,
        spec_version: Optional[str] = None,
    ) -> Optional[NodeDefinitionDetail]:
        if not node_id.startswith("builtin:"):
            return None
        type_name = node_id.removeprefix("builtin:").upper()
        try:
            node_type = NodeType(type_name)
        except ValueError:
            return None
        if node_type in _UNLISTED or node_type not in BUILTIN_SPECS:
            return None
        if spec_version is not None and spec_version != _BUILTIN_SPEC_VERSION:
            raise ValueError(
                f"builtin node {node_id!r} has spec_version={_BUILTIN_SPEC_VERSION!r}, "
                f"pinned={spec_version!r}"
            )
        return _build_detail(BUILTIN_SPECS[node_type])
