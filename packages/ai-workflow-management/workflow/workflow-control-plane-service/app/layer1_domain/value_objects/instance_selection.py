"""SA-2424 — per-run worker-instance selection (debug routing).

A run may pin which worker INSTANCE executes its nodes, so a run is
deterministic and debuggable when a worker_type has several online replicas
(e.g. one started locally + one on the server). Two authoring modes collapse
onto ONE resolved value per node here — the FE only builds the maps; this is
where the backend decides.

Shape (stored on the run / in AIW_WORKFLOW_WORKER_CONFIG, resolved per node at
dispatch). SA-2437: a pin value is ``{instance_id, host}`` (host = the human
label from the /workers page; used as a routing fallback when instance_id churns
on the same machine). A bare string value is still accepted (back-compat):

    {
      "mode": "WHOLE_FLOW" | "PER_NODE",
      "by_worker_type": {"<worker_type>": {"instance_id": "...", "host": "..."}},
      "by_node":        {"<node_id>":    {"instance_id": "...", "host": "..."}},
    }

Precedence per node: by_node[node_id] → by_worker_type[worker_type] → "".
An empty result means "no pin" — the executor keeps its normal
freshest-healthy selection (no behaviour change).
"""
from __future__ import annotations

from typing import Any

# SA-2437: a pin's stored value carries these keys (when in object form).
PIN_INSTANCE_ID = "instance_id"
PIN_HOST = "host"

MODE_WHOLE_FLOW = "WHOLE_FLOW"
MODE_PER_NODE = "PER_NODE"
VALID_MODES = (MODE_WHOLE_FLOW, MODE_PER_NODE)

# Reserved key under WorkflowRun.metadata that carries the selection. The
# ``__`` prefix marks it engine-internal (like ``__credentials``) so it is not
# mistaken for user metadata.
SELECTION_METADATA_KEY = "__instance_selection"


def _pin_value(raw: Any) -> tuple[str, str]:
    """Normalize a pin value to ``(instance_id, host)``.

    Accepts the SA-2437 object form ``{"instance_id","host"}`` and the legacy
    bare-string form (instance_id only). Anything else → ("","").
    """
    if isinstance(raw, dict):
        return str(raw.get(PIN_INSTANCE_ID) or ""), str(raw.get(PIN_HOST) or "")
    if isinstance(raw, str):
        return raw, ""
    return "", ""


def _resolve_pin(selection: Any, node_id: str, worker_type: str | None) -> tuple[str, str]:
    """The pinned ``(instance_id, host)`` for one node, or ("","") when unpinned.

    Precedence by_node → by_worker_type. Tolerant of malformed selection so the
    dispatch path never raises on bad run metadata.
    """
    if not isinstance(selection, dict):
        return "", ""
    by_node = selection.get("by_node")
    if isinstance(by_node, dict) and by_node.get(node_id):
        iid, host = _pin_value(by_node.get(node_id))
        if iid or host:
            return iid, host
    if worker_type:
        by_wt = selection.get("by_worker_type")
        if isinstance(by_wt, dict) and by_wt.get(worker_type):
            iid, host = _pin_value(by_wt.get(worker_type))
            if iid or host:
                return iid, host
    return "", ""


def resolve_target_instance_id(
    selection: Any, node_id: str, worker_type: str | None
) -> str:
    """The pinned instance_id for one node, or "" when unpinned."""
    return _resolve_pin(selection, node_id, worker_type)[0]


def resolve_target_host(
    selection: Any, node_id: str, worker_type: str | None
) -> str:
    """The pinned host label for one node, or "" when unpinned (SA-2437)."""
    return _resolve_pin(selection, node_id, worker_type)[1]


def to_run_selection(mode: str, selection: Any) -> dict:
    """Build the run-applicable selection for the active mode (SA-2437).

    A saved worker-config stores BOTH maps (so the UI keeps choices when toggling
    modes), but a run applies only the active mode's map. Returns {} when the
    active map is empty (→ no pin).
    """
    sel = selection if isinstance(selection, dict) else {}
    if (mode or "").upper() == MODE_PER_NODE:
        by_node = sel.get("by_node") or {}
        return {"mode": MODE_PER_NODE, "by_node": by_node, "by_worker_type": {}} if by_node else {}
    by_wt = sel.get("by_worker_type") or {}
    return {"mode": MODE_WHOLE_FLOW, "by_worker_type": by_wt, "by_node": {}} if by_wt else {}


def normalize_selection(selection: Any) -> dict:
    """Structurally validate + clean a caller-supplied selection.

    Keeps only string→string entries and a recognized mode. Raises ValueError
    on a present-but-invalid mode so a typo fails loudly at ``POST /runs``
    rather than silently pinning nothing. Deep validation (does the instance
    exist / is it online / does its worker_type match) happens at dispatch,
    where the executor fails the task fast (INSTANCE_PIN_UNAVAILABLE) — WCP
    stays decoupled from the registry at run-start.
    """
    if not selection:
        return {}
    if not isinstance(selection, dict):
        raise ValueError("instance_selection must be an object")
    mode = selection.get("mode") or ""
    if mode and mode not in VALID_MODES:
        raise ValueError(
            f"instance_selection.mode must be one of {VALID_MODES}, got {mode!r}"
        )

    def _clean_value(v: Any) -> Any:
        # Object form {instance_id, host} → keep only those keys (drop if both
        # empty); legacy bare string → keep as-is.
        if isinstance(v, dict):
            iid = str(v.get(PIN_INSTANCE_ID) or "")
            host = str(v.get(PIN_HOST) or "")
            if not iid and not host:
                return None
            out = {}
            if iid:
                out[PIN_INSTANCE_ID] = iid
            if host:
                out[PIN_HOST] = host
            return out
        if isinstance(v, str) and v:
            return v
        return None

    def _clean(m: Any) -> dict:
        if not isinstance(m, dict):
            return {}
        cleaned = {}
        for k, v in m.items():
            if not (k and isinstance(k, str)):
                continue
            cv = _clean_value(v)
            if cv is not None:
                cleaned[str(k)] = cv
        return cleaned

    by_worker_type = _clean(selection.get("by_worker_type"))
    by_node = _clean(selection.get("by_node"))
    if not by_worker_type and not by_node:
        return {}
    out: dict = {"by_worker_type": by_worker_type, "by_node": by_node}
    if mode:
        out["mode"] = mode
    return out
