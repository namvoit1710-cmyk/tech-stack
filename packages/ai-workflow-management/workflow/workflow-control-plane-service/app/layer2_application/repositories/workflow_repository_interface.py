from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Protocol

from app.layer1_domain.entities.workflow import Workflow


@dataclass
class ListWorkflowsFilter:
    status: str | None = None
    tenant_id: str | None = None
    main_flow: bool | None = None
    skip: int = 0
    top: int = 20
    orderby: str = ""
    eq: dict[str, str] = field(default_factory=dict)
    ne: dict[str, str] = field(default_factory=dict)
    gt: dict[str, str] = field(default_factory=dict)
    ge: dict[str, str] = field(default_factory=dict)
    lt: dict[str, str] = field(default_factory=dict)
    le: dict[str, str] = field(default_factory=dict)
    contains: dict[str, str] = field(default_factory=dict)
    id: str | None = None
    version: str | None = None
    # When true, the "current row per ID" resolution prefers a PUBLISHED version
    # over a later DRAFT edit (else latest-edited wins, the default — see
    # hana_workflow_repo._current_window). Opt-in per request so the editor's
    # default latest-edited view (SA-1491) is unchanged.
    prefer_published: bool = False


@dataclass
class ListWorkflowsPage:
    items: list[Workflow] = field(default_factory=list)
    total: int = 0


# The trigger types that start a run with NO human in the loop. Only these are
# gated on PUBLISHED (see TriggerRoute); ``manual`` needs no route because the
# caller names the workflow.
AUTOMATIC_TRIGGER_TYPES: tuple[str, ...] = ("event", "schedule")


@dataclass(frozen=True)
class TriggerRoute:
    """One automatic-trigger route: a PUBLISHED main flow × one TRIGGER node.

    Deliberately **not** a ``Workflow``. The scan that produces these reads
    three columns (``ID``, ``VERSION``, ``NODES``); a ``Workflow`` built from
    three columns would hand callers an entity whose ``edges``, ``name`` and
    schemas are silently empty — the exact failure shape of the DROP CONTEXT
    regressions, where a half-populated aggregate read back as "no data"
    instead of raising. A distinct, frozen type cannot lie that way, and it
    also states in the type what routing is actually allowed to depend on.

    ``workflow_id`` is the LOGICAL id (``AIW_WORKFLOWS.ID``), shared across a
    workflow's version rows — the value ``StartRunCommand.workflow_id`` takes.
    Verified live: the 5 rows of workflow 8728b765 all carry ID=8728b765 while
    ``WORKFLOW_ID`` is a fresh uuid per save, i.e. not an identity at all.
    """

    workflow_id: str
    version: str
    node_id: str
    trigger_type: str
    event_topic: str = ""
    event_filter: str = ""
    schedule_cron: str = ""


def _node_view(node: Any) -> tuple[str, str, Any]:
    """``(node_id, NODE_TYPE, config-carrier)`` for a node from either source.

    The HANA scan hands over raw ``NODES`` entries (dicts); the memory repo
    hands over ``WorkflowNode``s. Both end up at the SAME ``TriggerConfig``
    parser rather than two hand-rolled ``config.get(...) or input_binding.get(...)``
    readers that can drift from the spec.
    """
    if isinstance(node, dict):
        raw_type = node.get("node_type") or node.get("type") or ""
        return (
            str(node.get("id") or ""),
            str(raw_type).upper(),
            SimpleNamespace(config=node.get("config") or {}),
        )
    raw_type = getattr(node, "node_type", "")
    return (
        str(getattr(node, "id", "") or ""),
        str(getattr(raw_type, "value", raw_type)).upper(),
        node,
    )


def build_trigger_routes(
    workflow_id: str,
    version: str,
    nodes: Any,
    trigger_types: tuple[str, ...] = (),
) -> list[TriggerRoute]:
    """Turn one workflow ROW's nodes into its automatic-trigger routes.

    Shared by both repositories so the published-row rule and the config
    parsing live in exactly one place. ``trigger_types`` empty = every type.
    """
    from app.layer1_domain.node_spec.specs import TriggerConfig

    routes: list[TriggerRoute] = []
    for node in nodes or ():
        node_id, node_type, carrier = _node_view(node)
        if node_type != "TRIGGER":
            continue
        cfg = TriggerConfig.from_node(carrier)
        if trigger_types and cfg.trigger_type not in trigger_types:
            continue
        routes.append(TriggerRoute(
            workflow_id=workflow_id,
            version=version,
            node_id=node_id,
            trigger_type=cfg.trigger_type,
            event_topic=cfg.event_topic,
            event_filter=cfg.event_filter,
            schedule_cron=cfg.schedule_cron,
        ))
    return routes


class IWorkflowRepository(Protocol):
    def save(self, workflow: Workflow) -> None: ...
    def get_by_id(self, id: str) -> Workflow | None: ...
    def get_by_routing_path(self, routing_path: str) -> Workflow | None: ...
    def list_trigger_routes(
        self, trigger_types: tuple[str, ...] = (),
    ) -> list[TriggerRoute]: ...
    def list(self) -> list[Workflow]: ...
    def list_paged(self, filters: ListWorkflowsFilter) -> ListWorkflowsPage: ...
    def delete(self, id: str) -> None: ...
    def delete_all(self) -> int: ...
    def list_main_flows(self) -> list[Workflow]: ...
    def get_all_child_workflows(self, workflow_id: str) -> list[dict]: ...
    def save_workflow_relations(self, workflow_id: str, relations: list[tuple[str, str]]) -> None: ...
    def delete_workflow_relations(self, workflow_id: str) -> None: ...
    def get_child_workflow_ids(self, workflow_id: str) -> list[dict]: ...
    def get_workflows_by_ids(self, ids: list[str]) -> list[Workflow]: ...
    def get_latest_version_by_workflow_id(self, workflow_id: str) -> str | None: ...
    def get_by_id_version(self, id: str, version: str) -> Workflow | None: ...
    def get_by_latest_publish_version(self, id: str) -> Workflow | None: ...
    def get_list_version_by_status(self, id: str, status: str) -> list[Workflow]: ...

    async def aget_by_id(self, id: str) -> Workflow | None: ...
    async def aget_by_id_version(self, id: str, workflow_version: str) -> Workflow | None: ...
    async def aget_by_latest_publish_version(self, id: str) -> Workflow | None: ...
    async def asave(self, workflow: Workflow) -> None: ...
    async def aget_list_version_by_status(self, id: str, version: str, status: str) -> list[Workflow]: ...