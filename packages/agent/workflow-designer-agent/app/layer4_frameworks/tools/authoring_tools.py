from __future__ import annotations

import difflib
from typing import Any

from agent_sdk import tool

from app.layer1_domain.entities.workflow_definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowValidationError,
)
from app.layer1_domain.interfaces.workflow_control_plane_interface import (
    IWorkflowControlPlane,
    WorkflowControlPlaneError,
)

# Horizontal spacing for auto-layout when the model omits x/y (canvas layout only — not
# node config; without coordinates every node stacks at the origin).
_NODE_SPACING_X = 260


def _apply_default_layout(definition: WorkflowDefinition) -> None:
    for index, node in enumerate(definition.nodes):
        if node.x is None:
            node.x = float(index * _NODE_SPACING_X)
        if node.y is None:
            node.y = 0.0


def _build(
    name: object, description: object, nodes: object, edges: object
) -> WorkflowDefinition:
    """Validate raw tool input into a laid-out definition document, or raise."""
    definition = WorkflowDefinition.from_raw(name, description, nodes or [], edges or [])
    _apply_default_layout(definition)
    return definition


def _build_partial(
    name: object, description: object, nodes: object, edges: object
) -> dict:
    """Narrow a partial diff candidate (may be a single changed node) into a definition
    document, WITHOUT the whole-graph rules (>=2 nodes, connected). Those run on the MERGED
    graph in the gate. Still validates each node/edge shape."""
    if not isinstance(name, str) or not name.strip():
        raise WorkflowValidationError("Workflow name is required.")
    if nodes is not None and not isinstance(nodes, list):
        raise WorkflowValidationError("nodes must be a list.")
    if edges is not None and not isinstance(edges, list):
        raise WorkflowValidationError("edges must be a list.")
    definition = WorkflowDefinition(
        name=name.strip(),
        description=description.strip() if isinstance(description, str) else "",
        nodes=[WorkflowNode.from_raw(n) for n in (nodes or [])],
        edges=[WorkflowEdge.from_raw(e) for e in (edges or [])],
    )
    _apply_default_layout(definition)
    return definition.to_document()


def build_authoring_tools(control_plane: IWorkflowControlPlane) -> list[Any]:
    """Validate / create / update / run tools bound to the control-plane client.

    create_workflow / update_workflow only VALIDATE + lay out the candidate graph — they no
    longer persist. Persisting (create-until-first-success, then update), dry-running,
    classifying failures and repairing is owned by the ValidateRepairGate (Layer2), which
    runs after the designer hands back the candidate. run_workflow still calls the
    control-plane for real, but it is never bound to the LLM during the autonomous
    design -> validate -> repair flow (see ToolLoopWorkflowDesigner) — only an explicit
    user re-test of an already-created workflow may invoke it.
    """

    @tool("get_workflow", internal=True)
    async def get_workflow(workflow_id: str) -> dict:
        """Load an EXISTING workflow's current definition (by its REAL id) so you can change it.

        Call this before planning a change to a workflow that already exists — so you edit the
        real graph instead of rebuilding from scratch. You must pass the workflow's real ID, NOT
        its name: if the user only gave a NAME (e.g. "Get Weather Data for Vietnam"), call
        find_workflows(query=that name) FIRST to resolve the id, then call this with that id.
        Reuse each returned node "id" for nodes you change, and reference them in edges/bindings.

        Args:
            workflow_id: The real id of the workflow to load (not its name).
        Returns:
            {"status": "loaded"|"needs_lookup"|"error", "workflow_id", "definition"} where
            definition is {"name","description","nodes":[{"id","type","name","config"}],
            "edges":[{"source","target"}]}.
        """
        wid = (workflow_id or "").strip()
        if not wid:
            return {"status": "error", "message": "get_workflow needs a workflow_id."}
        # A human-typed NAME (has spaces) is never a real id — route it through find_workflows so
        # the right workflow is resolved (and a typo can be confirmed) instead of erroring.
        if " " in wid:
            return {
                "status": "needs_lookup",
                "message": (f"'{wid}' looks like a workflow NAME, not an id. Call "
                            f"find_workflows(query='{wid}') to resolve it to an id first, then "
                            "call get_workflow with that id."),
            }
        try:
            definition = await control_plane.get(wid)
        except WorkflowControlPlaneError as exc:
            return {
                "status": "error", "code": exc.code,
                "message": (f"{exc.message} If you only have the workflow's name (not its id), "
                            "call find_workflows to find it first."),
            }
        return {"status": "loaded", "workflow_id": wid, "definition": definition}

    @tool("find_workflows", internal=True)
    async def find_workflows(query: str) -> dict:
        """Resolve a workflow the user named (no id given) to its id, by matching the name.

        Call this FIRST whenever the user refers to an existing workflow BY NAME rather than id
        (e.g. "clone 'Get Weather Data for Vietnam' but use Hai Phong", "update my weather flow")
        and no workflow_id/source_workflow_id is in the context — you need the real id before
        get_workflow. Matching is case-insensitive: exact and substring hits rank first, then
        close (typo-tolerant) matches. If the best match's name differs from what the user typed,
        treat it as a likely typo and CONFIRM with the user before acting. If several match, ask
        which one. If nothing matches, tell the user and offer to create a new workflow.

        Args:
            query: The workflow name (or part of it) the user referred to.
        Returns:
            {"status": "ok"|"error", "query", "matches": [{"workflow_id","name","description",
            "match": "exact"|"contains"|"close"}]} — best match first (max 10).
        """
        q = (query or "").strip()
        if not q:
            return {"status": "error", "message": "find_workflows needs a name to search for."}
        try:
            workflows = await control_plane.list()
        except WorkflowControlPlaneError as exc:
            return {"status": "error", "code": exc.code, "message": exc.message}
        ql = q.lower()
        ranked = []
        for wf in workflows:
            name = str(wf.get("name") or "")
            nl = name.lower()
            if nl == ql:
                kind, score = "exact", 3.0
            elif ql in nl or nl in ql:
                kind, score = "contains", 2.0
            else:
                ratio = difflib.SequenceMatcher(None, ql, nl).ratio()
                if ratio < 0.6:  # too far to be a plausible typo — skip
                    continue
                kind, score = "close", ratio
            ranked.append((score, {
                "workflow_id": wf.get("id"),
                "name": name,
                "description": wf.get("description") or "",
                "match": kind,
            }))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return {"status": "ok", "query": q, "matches": [m for _, m in ranked[:10]]}

    @tool("validate_workflow", internal=True)
    async def validate_workflow(
        name: str,
        description: str = "",
        nodes: list | None = None,
        edges: list | None = None,
    ) -> dict:
        """Dry-check a workflow definition WITHOUT saving it.

        Call before create_workflow / update_workflow to catch structural problems early
        (needs >=2 nodes, unique ids, edges must reference known nodes, no isolated nodes).
        If valid=false, fix exactly the reported error and validate again.

        Args:
            name: Workflow name.
            description: What the workflow does.
            nodes: Node list; each is {"id","type","name","config", optional "x","y"}.
            edges: Connections; each is {"source","target"} referencing node ids.
        Returns:
            {"valid": bool, "error": str}
        """
        try:
            WorkflowDefinition.from_raw(name, description, nodes or [], edges or [])
        except WorkflowValidationError as exc:
            return {"valid": False, "error": str(exc)}
        return {"valid": True, "error": ""}

    @tool("create_workflow", internal=True)
    async def create_workflow(
        name: str,
        description: str = "",
        nodes: list | None = None,
        edges: list | None = None,
    ) -> dict:
        """Finalize a NEW workflow's design (structural check only — no persistence yet).

        Use for a brand-new workflow. If the conversation already has a workflow and the
        user is refining it, use update_workflow instead. Provide the FULL node graph with
        each node's config filled from its get_node_schema result. The validate/repair gate
        persists, dry-runs and test-runs the graph after you hand it over here.

        Args:
            name: Workflow name.
            description: What the workflow does.
            nodes: Full node list; each {"id","type","name","config", optional "x","y"}. >=2.
            edges: Connections; each {"source","target"} referencing node ids.
        Returns:
            {"status": "ready"|"invalid", "definition", "error"}
        """
        try:
            definition = _build(name, description, nodes, edges)
        except WorkflowValidationError as exc:
            return {"status": "invalid", "error": str(exc)}
        return {"status": "ready", "definition": definition.to_document()}

    @tool("update_workflow", internal=True)
    async def update_workflow(
        workflow_id: str,
        name: str,
        description: str = "",
        nodes: list | None = None,
        edges: list | None = None,
        remove_node_ids: list | None = None,
    ) -> dict:
        """Finalize a change to an EXISTING workflow (structural check only — the gate persists).

        Use when the user refines a workflow that already exists (get_workflow it first). You do
        NOT have to re-send unchanged nodes: send only the nodes you ADD or CHANGE (reusing the
        real ids from get_workflow for changed ones), plus the edges among the nodes you touched;
        the platform preserves every node/edge you don't mention. To DELETE a node, put its id in
        remove_node_ids. The merged graph is still validated + dry-run + test-run before it saves,
        and the workflow keeps its id.

        Args:
            workflow_id: Id of the workflow to update.
            name: Workflow name.
            description: What the workflow does.
            nodes: The nodes to add/change; each {"id","type","name","config", optional "x","y"}.
            edges: Connections among the touched nodes; each {"source","target"}.
            remove_node_ids: Ids of existing nodes to delete (optional).
        Returns:
            {"status": "ready"|"invalid"|"error", "workflow_id", "definition", "remove_node_ids", ...}
        """
        if not (workflow_id or "").strip():
            return {"status": "error", "message": "update_workflow needs the existing workflow_id."}
        removals = [str(x) for x in (remove_node_ids or []) if str(x).strip()]
        # Structural validation runs on the MERGED graph in the gate (this candidate may be a
        # partial diff), so only narrow the raw shapes here — don't enforce the >=2 / connected
        # rules that assume a complete graph.
        try:
            definition = _build_partial(name, description, nodes, edges)
        except WorkflowValidationError as exc:
            return {"status": "invalid", "error": str(exc)}
        return {
            "status": "ready",
            "workflow_id": workflow_id,
            "definition": definition,
            "remove_node_ids": removals,
        }

    @tool("clone_workflow", internal=True)
    async def clone_workflow(
        source_workflow_id: str,
        name: str,
        description: str = "",
        nodes: list | None = None,
        edges: list | None = None,
        remove_node_ids: list | None = None,
    ) -> dict:
        """Finalize a NEW workflow CLONED from an existing one (structural check only).

        Use when the user wants a new workflow that is a variation of an existing one
        (e.g. "make a copy of workflow X but send to Slack instead of email"). get_workflow the
        SOURCE first, then call this: the source is used as a TEMPLATE and is NOT modified — the
        platform loads it, overlays your changes and saves the result as a brand-new workflow with
        its own id. Like update_workflow you send ONLY the nodes you ADD or CHANGE (reuse the
        source's real node ids for changed ones), plus the edges among the nodes you touched, and
        list ids to drop in remove_node_ids; every node/edge you don't mention is copied from the
        source. The merged graph is validated + dry-run + test-run before it saves.

        Args:
            source_workflow_id: Id of the existing workflow to clone from (left unchanged).
            name: Name for the NEW workflow.
            description: What the new workflow does.
            nodes: The nodes to add/change vs the source; each {"id","type","name","config", opt "x","y"}.
            edges: Connections among the touched nodes; each {"source","target"}.
            remove_node_ids: Ids of source nodes to omit from the clone (optional).
        Returns:
            {"status": "ready"|"invalid"|"error", "source_workflow_id", "definition", "remove_node_ids", ...}
        """
        if not (source_workflow_id or "").strip():
            return {"status": "error", "message": "clone_workflow needs the source_workflow_id."}
        removals = [str(x) for x in (remove_node_ids or []) if str(x).strip()]
        # Same partial-diff semantics as update_workflow: the candidate is only the added/changed
        # nodes and is merged onto the loaded source in the gate, so the whole-graph rules
        # (>=2 / connected) run there, not here.
        try:
            definition = _build_partial(name, description, nodes, edges)
        except WorkflowValidationError as exc:
            return {"status": "invalid", "error": str(exc)}
        return {
            "status": "ready",
            "source_workflow_id": source_workflow_id,
            "definition": definition,
            "remove_node_ids": removals,
        }

    @tool("run_workflow", internal=True)
    async def run_workflow(workflow_id: str) -> dict:
        """Test-run a persisted workflow and report the outcome.

        Call this ONLY when the user asks to run/test it (or confirms). On a run error,
        read ``run_error``, fix the definition with update_workflow, and run again.

        Args:
            workflow_id: Id of the workflow to run (from create_workflow / update_workflow).
        Returns:
            {"status": "run"|"error", "run_status", "run_id", "run_error", "link"}
        """
        if not (workflow_id or "").strip():
            return {"status": "error", "message": "run_workflow needs a workflow_id."}
        try:
            outcome = await control_plane.test_run(workflow_id, {})
        except WorkflowControlPlaneError as exc:
            return {"status": "error", "code": exc.code, "message": exc.message}
        return {
            "status": "run",
            "run_status": outcome.status,
            "run_id": outcome.run_id,
            "run_error": outcome.error,
            "link": outcome.replay_link,
        }

    return [find_workflows, get_workflow, validate_workflow, create_workflow, update_workflow, clone_workflow, run_workflow]
