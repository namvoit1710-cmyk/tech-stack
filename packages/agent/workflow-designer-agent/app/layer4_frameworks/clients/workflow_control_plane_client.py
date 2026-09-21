from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import httpx

from app.layer1_domain.entities.workflow_run import DryRunOutcome, RunOutcome, WorkflowRef
from app.layer1_domain.interfaces.workflow_control_plane_interface import (
    IWorkflowControlPlane,
    WorkflowControlPlaneError,
)
from app.layer2_application.interfaces.catalog_provider_interface import ICatalogProvider

logger = logging.getLogger(__name__)

# Generic fallbacks used ONLY when the live catalog doesn't advertise a node's ports
# (e.g. the catalog is unreachable). These are not per-node-type config — the real port
# handles are resolved from the catalog below.
_DEFAULT_OUT_PORT = "out_default"
_DEFAULT_IN_PORT = "in_default"

# A binding expression references a node by ``$<node_id>`` (e.g. {{$trigger.output.file}}),
# so when a node id is rewritten to a UUID the same token in every parameter must follow.
_BINDING_REF_RE = re.compile(r"\$([A-Za-z0-9_-]+)")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _remap_bindings(value: Any, id_map: dict[str, str]) -> Any:
    """Rewrite ``$<old_id>`` refs to ``$<new_id>`` inside any parameter value (str / nested
    dict / list). ``$input`` and any token not in ``id_map`` (e.g. output field names) are
    left untouched."""
    if isinstance(value, str):
        return _BINDING_REF_RE.sub(
            lambda m: "$" + id_map.get(m.group(1), m.group(1)), value
        )
    if isinstance(value, dict):
        return {k: _remap_bindings(v, id_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap_bindings(v, id_map) for v in value]
    return value


class HttpWorkflowControlPlane(IWorkflowControlPlane):
    """Persists and test-runs workflows via the workflow-control-plane REST API.

    Translates this agent's definition document (nodes {id,type,name,config},
    edges {source,target}) into the control-plane payload (node_type, source_node_id,
    target_node_id) and unwraps its {status, data|error} envelope. Edge port handles are
    resolved from the LIVE node catalog (a node type's real ``ports.in/out``) rather than a
    hardcoded per-type table, so new node types wire correctly with no code change.
    """

    def __init__(
        self,
        base_url: str,
        app_url: str = "",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        catalog: ICatalogProvider | None = None,
    ) -> None:
        self._base = base_url.rstrip("/") + "/api/v1"
        self._app_url = app_url.rstrip("/")
        self._client = client
        self._timeout = timeout
        self._catalog = catalog
        self._ports: dict[str, tuple[str, str]] | None = None  # key -> (in_port, out_port)
        self._id_to_type: dict[str, str] = {}  # catalog id -> node_type (upper)
        self._by_ref: dict[str, Any] = {}  # catalog id AND node_type (upper) -> CatalogNode

    async def get(self, workflow_id: str) -> dict:
        """Load an existing workflow (GET /workflows/{id}) and map it back to this agent's
        definition shape so the model and the diff-merge reason over one consistent shape."""
        data = await self._get(f"/workflows/{workflow_id}")
        definition = await self._from_control_plane_payload(data)
        logger.info(
            "control-plane get: id=%s nodes=%d edges=%d",
            workflow_id, len(definition.get("nodes", [])), len(definition.get("edges", [])),
        )
        return definition

    async def list(self) -> list[dict]:
        """List workflows (GET /workflows) as {id, name, description} summaries so a named
        workflow can be resolved to its id. Returns the paged ``items`` (default page); the
        agent only needs enough to match a name, not the full graph."""
        data = await self._get("/workflows")
        items = data.get("items") if isinstance(data, dict) else []
        summaries = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            wid = str(item.get("id") or "")
            if not wid:
                continue
            summaries.append({
                "id": wid,
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
            })
        logger.info("control-plane list: %d workflows", len(summaries))
        return summaries

    async def patch(self, workflow_id: str, definition: dict) -> WorkflowRef:
        """Partial-persist an existing workflow (PATCH /workflows/{id}). The control plane
        replaces the nodes/edges it receives but keeps the workflow id stable and does not fork
        a new version — so this is the diff-update persist (unrelated nodes are already merged
        into ``definition`` by the caller)."""
        payload = await self._to_create_payload(definition)
        logger.info(
            "control-plane patch: id=%s nodes=%d edges=%d",
            workflow_id, len(payload.get("nodes", [])), len(payload.get("edges", [])),
        )
        data = await self._patch(f"/workflows/{workflow_id}", payload)
        new_id = data.get("id") or workflow_id
        logger.info("control-plane patched workflow_id=%s", new_id)
        return WorkflowRef(workflow_id=new_id, link=self._workflow_link(new_id))

    async def create(self, definition: dict) -> WorkflowRef:
        payload = await self._to_create_payload(definition)
        logger.info(
            "control-plane create: name=%s nodes=%d edges=%d",
            payload.get("name"), len(payload.get("nodes", [])), len(payload.get("edges", [])),
        )
        data = await self._post("/workflows", payload)
        workflow_id = data.get("id") or ""
        if not workflow_id:
            raise WorkflowControlPlaneError("NO_WORKFLOW_ID", "Create response had no workflow id", data)
        logger.info("control-plane created workflow_id=%s", workflow_id)
        return WorkflowRef(workflow_id=workflow_id, link=self._workflow_link(workflow_id))

    async def update(self, workflow_id: str, definition: dict) -> WorkflowRef:
        """Full-replace an existing workflow (PUT /workflows/{id}) with a rebuilt definition."""
        payload = await self._to_create_payload(definition)
        logger.info(
            "control-plane update: id=%s nodes=%d edges=%d",
            workflow_id, len(payload.get("nodes", [])), len(payload.get("edges", [])),
        )
        data = await self._put(f"/workflows/{workflow_id}", payload)
        new_id = data.get("id") or workflow_id
        logger.info("control-plane updated workflow_id=%s", new_id)
        return WorkflowRef(workflow_id=new_id, link=self._workflow_link(new_id))

    async def test_run(
        self, workflow_id: str, input_data: dict, skip_validation: bool = True
    ) -> RunOutcome:
        data = await self._post(
            f"/workflows/{workflow_id}/test/full",
            {"input_data": input_data or {}, "skip_validation": skip_validation},
        )
        run_id = data.get("run_id") or ""
        logger.info(
            "control-plane test/full workflow_id=%s run_id=%s status=%s skip_validation=%s",
            workflow_id, run_id, data.get("status"), skip_validation,
        )
        return RunOutcome(
            status=data.get("status") or "",
            run_id=run_id,
            error=data.get("error") or "",
            error_code=data.get("error_code") or "",
            details=data.get("error_details") or {},
            replay_link=self._replay_link(workflow_id, run_id),
        )

    async def dry_run(self, workflow_id: str, input_data: dict | None = None) -> DryRunOutcome:
        """Side-effect-free simulation (POST /workflows/{id}/dry-run) — no node actually runs."""
        data = await self._post(f"/workflows/{workflow_id}/dry-run", {"input_data": input_data or {}})
        is_valid = bool(data.get("is_valid"))
        logger.info("control-plane dry-run workflow_id=%s is_valid=%s", workflow_id, is_valid)
        return DryRunOutcome(
            ok=is_valid,
            error_code=data.get("error_code") or "",
            error_message=data.get("error") or "; ".join(
                [*data.get("validation_errors", []), *data.get("schema_errors", [])]
            ),
            error_details=data.get("error_details") or {},
        )

    # --- port resolution (catalog-driven) ---

    async def _port_map(self) -> dict[str, tuple[str, str]]:
        """Map node_type (upper) AND catalog id -> (first in-port, first out-port), from
        the live palette; also cache catalog id -> node_type. Empty when no catalog."""
        if self._ports is not None:
            return self._ports
        mapping: dict[str, tuple[str, str]] = {}
        id_to_type: dict[str, str] = {}
        by_ref: dict[str, Any] = {}
        if self._catalog is not None:
            try:
                for node in await self._catalog.nodes():
                    entry = (
                        node.in_ports[0] if node.in_ports else _DEFAULT_IN_PORT,
                        node.out_ports[0] if node.out_ports else _DEFAULT_OUT_PORT,
                    )
                    if node.node_type:
                        mapping[node.node_type.upper()] = entry
                        by_ref.setdefault(node.node_type.upper(), node)
                    if node.id:
                        mapping[node.id] = entry
                        by_ref[node.id] = node
                        if node.node_type:
                            id_to_type[node.id] = node.node_type.upper()
            except Exception as exc:  # catalog down -> generic fallbacks only
                logger.info("catalog index unavailable (%s)", exc)
        self._ports = mapping
        self._id_to_type = id_to_type
        self._by_ref = by_ref
        return self._ports

    def _in_port(self, mapping: dict, keys: list[str]) -> str:
        for key in keys:
            hit = mapping.get(key) or mapping.get((key or "").upper())
            if hit:
                return hit[0]
        return _DEFAULT_IN_PORT

    def _out_port(self, mapping: dict, keys: list[str]) -> str:
        for key in keys:
            hit = mapping.get(key) or mapping.get((key or "").upper())
            if hit:
                return hit[1]
        return _DEFAULT_OUT_PORT

    async def _to_create_payload(self, definition: dict) -> dict[str, Any]:
        ports = await self._port_map()
        id_to_type = self._id_to_type
        by_ref = self._by_ref
        raw_nodes = definition.get("nodes", []) or []
        # Give every node a UUID id (palette-created nodes use UUIDs; the model hands us short
        # handles like "http_1"). Keep an id that is already a UUID so a re-save is stable.
        # The handles are only used to wire edges + bindings, so remap those to the new ids.
        id_map: dict[str, str] = {}
        for node in raw_nodes:
            old = str(node.get("id") or "")
            if old and old not in id_map:
                id_map[old] = old if _is_uuid(old) else str(uuid.uuid4())
        keys_by_id: dict[str, list[str]] = {}  # node id -> lookup keys (worker_type, node_type)
        nodes = []
        for node in raw_nodes:
            new_id = id_map.get(str(node.get("id") or ""), str(node.get("id") or ""))
            # The model's config fields go in `parameters` — the control plane's unified
            # field, which it spec-splits into engine config + input_binding per node spec.
            # Rewrite binding refs ($old_id -> $new_id) so they follow the UUID rename.
            parameters = _remap_bindings(dict(node.get("config") or {}), id_map)
            worker_type = parameters.pop("worker_type", None)
            raw_type = str(node.get("type") or "").strip()
            # Resolve the node against the LIVE catalog. The model identifies a node by its
            # catalog id (e.g. "worker:http_request", "builtin:output"); we resolve the real
            # node_type enum and ENRICH the definition-sourced fields the control plane stores
            # verbatim but does not backfill for TASK workers — mirroring the FE palette drop
            # (worker_type = the catalog id, plus icon/color/node_definition_id). Falls back to
            # a bare upper-cased type when the catalog can't resolve the node.
            entry = by_ref.get(raw_type) or (by_ref.get(worker_type) if worker_type else None) \
                or by_ref.get(raw_type.upper())
            icon = color = node_definition_id = None
            if entry is not None:
                node_type = (entry.node_type or raw_type).upper()
                # A worker-/business-backed node's catalog id IS its worker_type (FE parity);
                # builtins get their worker_type from the control plane, so leave them alone.
                if entry.id.startswith(("worker:", "business:")):
                    worker_type = worker_type or entry.id
                if entry.id.startswith("business:"):
                    node_definition_id = entry.id[len("business:"):]
                icon = entry.icon or None
                color = entry.color or None
            elif raw_type in id_to_type:
                node_type = id_to_type[raw_type]
                if raw_type.startswith("worker:"):
                    worker_type = worker_type or raw_type
            else:
                node_type = raw_type.upper()
            # Port handles follow the worker (for TASK nodes) then the node type.
            keys_by_id[new_id] = [k for k in (worker_type, node_type) if k]
            mapped: dict[str, Any] = {
                "id": new_id,
                "name": node.get("name"),
                "node_type": node_type,
                "parameters": parameters,
            }
            # Preserve canvas layout so nodes don't stack at the origin.
            if node.get("x") is not None:
                mapped["x"] = node["x"]
            if node.get("y") is not None:
                mapped["y"] = node["y"]
            # A TASK node is only runnable with a worker_type; lift it to the node field.
            if worker_type:
                mapped["worker_type"] = worker_type
            # Definition-sourced display/routing fields (only when the catalog resolved them).
            if node_definition_id:
                mapped["node_definition_id"] = node_definition_id
            if icon:
                mapped["icon"] = icon
            if color:
                mapped["color"] = color
            nodes.append(mapped)
        edges = []
        for index, edge in enumerate(definition.get("edges", []) or [], start=1):
            source = id_map.get(str(edge.get("source")), edge.get("source"))
            target = id_map.get(str(edge.get("target")), edge.get("target"))
            edges.append({
                "id": edge.get("id") or f"e{index}",
                "source_node_id": source,
                "target_node_id": target,
                # Wire to real port handles (from the live catalog) so the canvas draws it.
                "source_port_id": self._out_port(ports, keys_by_id.get(source, [])),
                "target_port_id": self._in_port(ports, keys_by_id.get(target, [])),
            })
        return {
            "name": definition.get("name", ""),
            "description": definition.get("description", ""),
            "nodes": nodes,
            "edges": edges,
        }

    async def _from_control_plane_payload(self, document: dict) -> dict[str, Any]:
        """Inverse of ``_to_create_payload``: turn a stored workflow document back into this
        agent's definition shape. A node's ``type`` is resolved to the catalog id the model
        works with (``business:<def-id>`` / ``worker_type`` / the builtin id for its node_type),
        so a subsequent re-persist round-trips cleanly through ``_to_create_payload``."""
        await self._port_map()  # populate self._by_ref for builtin-id resolution
        by_ref = self._by_ref
        document = document or {}
        nodes = []
        for node in (document.get("nodes") or []):
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("node_type") or node.get("type") or "")
            worker_type = str(node.get("worker_type") or "")
            node_def_id = str(node.get("node_definition_id") or "")
            if node_def_id:
                node_ref = f"business:{node_def_id}"
            elif worker_type:
                node_ref = worker_type
            else:
                entry = by_ref.get(node_type.upper())
                node_ref = entry.id if entry is not None and entry.id else node_type
            mapped: dict[str, Any] = {
                "id": node.get("id"),
                "type": node_ref,
                "name": node.get("name") or node.get("id"),
                "config": dict(node.get("parameters") or node.get("config") or {}),
            }
            if node.get("x") is not None:
                mapped["x"] = node["x"]
            if node.get("y") is not None:
                mapped["y"] = node["y"]
            nodes.append(mapped)
        edges = []
        for edge in (document.get("edges") or []):
            if not isinstance(edge, dict):
                continue
            edges.append({
                "source": edge.get("source_node_id") or edge.get("source"),
                "target": edge.get("target_node_id") or edge.get("target"),
            })
        return {
            "name": document.get("name", ""),
            "description": document.get("description", ""),
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def _unwrap(status_code: int, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise WorkflowControlPlaneError("BAD_RESPONSE", f"Non-object response (HTTP {status_code})")
        if status_code == 200 and body.get("status") == "ok":
            data = body.get("data")
            return data if isinstance(data, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        details = error.get("details") or error.get("error_details") or {}
        message = error.get("message") or f"Control plane returned HTTP {status_code}"
        # Surface the specific reason (e.g. "'worker:http_request' is not a valid NodeType")
        # so the agent's repair loop — and the user — see WHY, not just "Invalid node data".
        reason = details.get("reason") if isinstance(details, dict) else None
        if reason and reason not in message:
            message = f"{message}: {reason}"
        raise WorkflowControlPlaneError(
            error.get("code") or f"HTTP_{status_code}", message, details,
        )

    def _workflow_link(self, workflow_id: str) -> str:
        return f"{self._app_url}/workflow/{workflow_id}" if self._app_url else ""

    def _replay_link(self, workflow_id: str, run_id: str) -> str:
        if not self._app_url or not run_id:
            return self._workflow_link(workflow_id)
        return f"{self._app_url}/workflow/{workflow_id}/replay/{run_id}"

    async def _post(self, path: str, payload: dict) -> dict[str, Any]:
        return await self._send("POST", path, payload)

    async def _put(self, path: str, payload: dict) -> dict[str, Any]:
        return await self._send("PUT", path, payload)

    async def _patch(self, path: str, payload: dict) -> dict[str, Any]:
        return await self._send("PATCH", path, payload)

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._send("GET", path, None)

    async def _send(self, method: str, path: str, payload: dict | None) -> dict[str, Any]:
        if not self._base.startswith(("http://", "https://")):
            raise WorkflowControlPlaneError(
                "CONTROL_PLANE_NOT_CONFIGURED",
                "WORKFLOW_API_BASE_URL must be a full http(s) URL "
                f"(got '{self._base}'). Set it in the agent's .env.",
            )
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns = self._client is None
        try:
            resp = await client.request(method, f"{self._base}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise WorkflowControlPlaneError("CONTROL_PLANE_UNREACHABLE", str(exc)) from exc
        finally:
            if owns:
                await client.aclose()
        try:
            body = resp.json()
        except ValueError as exc:
            raise WorkflowControlPlaneError("NON_JSON_RESPONSE", resp.text[:200]) from exc
        return self._unwrap(resp.status_code, body)
