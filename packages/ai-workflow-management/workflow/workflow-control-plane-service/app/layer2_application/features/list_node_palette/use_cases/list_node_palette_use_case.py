"""List available node types for the workflow builder."""

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.monitor_interface import IMonitor
from app.layer2_application.interfaces.worker_registry_client_interface import IWorkerRegistryClient
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer1_domain.node_spec import BUILTIN_SPECS, get_spec
from app.layer1_domain.node_spec.form_renderer import (
    render_input_schema,
    render_output_schema,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.worker_ref import WorkerRef
from app.layer2_application.providers.worker_freshness import is_fresh, parse_ts


def _node(node_type: str) -> dict[str, Any]:
    """Palette entry for a builtin node type — everything from its spec."""
    spec = get_spec(NodeType(node_type))
    return {
        "id": f"builtin:{node_type.lower()}",
        "name": spec.name,
        "node_type": node_type,
        "category": "control_flow",  # legacy palette uses one flat bucket
        "description": spec.description,
        "worker_id": f"builtin:{node_type.lower()}",
        "spec_version": spec.spec_version,
        "capabilities": [],
        "input_schema": render_input_schema(spec),
        "output_schema": render_output_schema(spec),
        "node_class": spec.node_class.value,
        "icon": spec.icon,
        "color": spec.color,
        "tags": [],
        "ports": {
            "in": [p.to_dict() for p in spec.in_ports],
            "out": [p.to_dict() for p in spec.out_ports],
        },
    }


# The legacy palette's 13 entries (no OUTPUT/SET_VARIABLE — historical
# surface, superseded by /api/v2/node-catalog which lists everything).
BUILTIN_NODES: list[dict[str, Any]] = [
    _node("CONDITION"),
    _node("SWITCH"),
    _node("LOOP"),
    _node("PARALLEL"),
    _node("MERGE"),
    _node("TRIGGER"),
    _node("WORKFLOW"),
    _node("COMPUTE"),
    _node("INPUT"),
    _node("HUMAN_ACTION"),
    _node("DATA_EDIT"),
    _node("TEMPLATE_DATA_EDIT"),
    _node("FILE_UPLOAD"),
    _node("APPROVAL_FLOW"),
]


@dataclass
class ListNodePaletteCommand:
    tenant_id: str = "system"



@dataclass
class ListNodePaletteResult:
    categories: dict[str, Any] = field(default_factory=dict)
    total: int = 0



class ListNodePaletteUseCase:
    def __init__(self, logger: ILogger, worker_registry_client: Optional[IWorkerRegistryClient] = None,
                 node_definition_repo: Optional[INodeDefinitionRepository] = None,
                 monitor: Optional[IMonitor] = None, app_config: Optional[Any] = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.worker_registry_client = worker_registry_client
        self.node_definition_repo = node_definition_repo
        self.monitor = monitor
        # SA-2052 (R5): the stale-heartbeat window. The CP smart-DI pool injects
        # settings here by parameter name (available_dependencies["app_config"] =
        # settings, bootstrap.py:1001/:1073); when constructed without it (unit
        # tests) it falls back to 120 in _fetch_worker_nodes.
        self._app_config = app_config

    async def execute(self, command: ListNodePaletteCommand | None = None) -> ListNodePaletteResult:
        self.logger.info("Fetching node palette")
        # Phase 3 rollout observability: each legacy call is a client that
        # hasn't migrated to /api/v2/node-catalog yet. Counter should trend
        # toward zero before Phase 4 deletes the endpoint.
        if self.monitor is not None:
            try:
                self.monitor.increment("node_palette_legacy_requests_total")
            except Exception:
                pass
        control_flow_nodes = list(BUILTIN_NODES)
        worker_nodes = self._fetch_worker_nodes()
        tenant_id = command.tenant_id if command else ""
        business_nodes = self._fetch_business_nodes(tenant_id)
        total = len(control_flow_nodes) + len(worker_nodes) + len(business_nodes)
        return ListNodePaletteResult(
            categories={
                "control_flow": control_flow_nodes,
                "worker": worker_nodes,
                "business": business_nodes,
            },
            total=total,
        )

    @staticmethod
    def _build_node_types_summary() -> list[dict[str, Any]]:
        """Return a compact summary of every registered NodeType with v2 metadata."""
        result = []
        for ntype, spec in BUILTIN_SPECS.items():
            result.append({
                "node_type": ntype.value,
                "node_class": spec.node_class.value,
                "icon": spec.icon,
                "color": spec.color,
                "ports": {
                    "in": [p.to_dict() for p in spec.in_ports],
                    "out": [p.to_dict() for p in spec.out_ports],
                },
            })
        return result

    def _fetch_business_nodes(self, tenant_id: str) -> list[dict]:
        if not self.node_definition_repo or not tenant_id:
            return []
        try:
            definitions = self.node_definition_repo.list_by_tenant(tenant_id)
        except Exception as exc:
            self.logger.error("Failed to fetch business nodes", error=str(exc))
            return []
        nodes = []
        for nd in definitions:
            nodes.append({
                "id": f"business:{nd.id}",
                "node_definition_id": nd.id,
                "name": nd.name,
                "node_type": nd.base_type.value if hasattr(nd.base_type, "value") else str(nd.base_type),
                "node_class": "BUSINESS",
                "category": "business",
                "description": nd.description,
                "worker_id": nd.id,
                "spec_version": getattr(nd, "spec_version", "1.0.0") or "1.0.0",
                "input_schema": nd.input_schema,
                "output_schema": nd.output_schema,
                "icon": nd.icon,
                "color": nd.color,
                "tags": nd.tags,
                "ports": nd.ports,
            })
        return nodes

    def _fetch_worker_nodes(self) -> list[dict]:
        if not self.worker_registry_client:
            return []
        try:
            workers = self.worker_registry_client.list_workers()
        except Exception as exc:
            self.logger.error("Failed to fetch workers for palette", error=str(exc))
            return []
        # Mirror v2 (WorkerNodeProvider takes the int directly): honor an explicit
        # 0 rather than swallowing it with `or 120`. getattr's default covers
        # app_config=None / missing attr (unit tests) -> 120. (SA-2052 R5)
        stale_after = int(getattr(self._app_config, "WORKER_STALE_AFTER_SECONDS", 120))
        task_desc = get_spec(NodeType.TASK)
        # SA-2052 (R5): filter (health, empty worker_type, stale heartbeat), then
        # GROUP survivors by worker_type and emit ONE node per type — N replicas of
        # a type share id="worker:<type>" (the FE React key) and must collapse.
        survivors: dict[str, list[dict]] = {}
        for w in workers:
            if w.get("health_status") not in (None, "", "healthy", "active"):
                continue
            # Require stable worker_type; drop UUID fallback (unstable across restarts).
            worker_type = w.get("worker_type", "")
            if not worker_type:
                self.logger.warning(
                    "palette skipping worker with empty worker_type",
                    worker_registration_id=w.get("id"),
                )
                continue
            if not is_fresh(w, stale_after):
                continue
            survivors.setdefault(worker_type, []).append(w)

        nodes = []
        for worker_type, rows in survivors.items():
            # Representative = freshest healthy replica (spec §5). Absent/blank
            # last_heartbeat -> datetime.min, so it only wins when it is the sole row.
            w = max(rows, key=lambda r: parse_ts(r.get("last_heartbeat", "")))
            worker_id = worker_type
            name = w.get("name", "unknown-worker")
            capabilities = w.get("capabilities", [])
            cap_summary = ", ".join(
                f"{c.get('domain', '?')}.{c.get('action', '?')}" for c in capabilities
            ) if capabilities else "general"
            description = w.get("description") or f"Executes tasks via '{name}'. Capabilities: {cap_summary}."

            # Use worker-provided v2 metadata or fall back to TASK node defaults
            node_class = w.get("node_class") or (task_desc.node_class.value if task_desc else "BUSINESS")
            icon = w.get("icon") or (task_desc.icon if task_desc else "Cog")
            color = w.get("color") or (task_desc.color if task_desc else "#3B82F6")
            tags = w.get("tags") or []
            ports = w.get("ports") or (
                {
                    "in": [p.to_dict() for p in task_desc.in_ports],
                    "out": [p.to_dict() for p in task_desc.out_ports],
                } if task_desc else {"in": [], "out": []}
            )

            nodes.append({
                "id": WorkerRef(worker_type=worker_id).catalog_id,
                "name": name,
                "node_type": "TASK",
                "category": "worker",
                "description": description,
                "worker_id": worker_id,
                "worker_type": w.get("worker_type", ""),
                "version": w.get("version", ""),
                "spec_version": w.get("spec_version") or "1.0.0",
                "capabilities": capabilities,
                "input_schema": w.get("input_schema") or [],
                "output_schema": w.get("output_schema") or [],
                "config_hints": {},
                "node_class": node_class,
                "icon": icon,
                "color": color,
                "tags": tags,
                "ports": ports,
            })
        return nodes


