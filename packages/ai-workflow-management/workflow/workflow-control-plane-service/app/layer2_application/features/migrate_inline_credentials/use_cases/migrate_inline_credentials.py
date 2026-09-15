"""T5 migration — lift existing inline node secrets into the vault as credential_refs.

The T5 hard-remove deleted every worker's inline secret *fields*, so newly-authored
nodes can only carry a ``credential_ref``. Workflows authored *before* the cut still hold
plaintext secrets in their node ``parameters``/``config``; this one-shot migration walks
those nodes, issues a vault credential from each inline secret, rewrites the node to a
``credential_ref``, and strips the plaintext. It is the ``findings.md §6`` migration gate:
**run it (per tenant) before the inline fields are gone in prod**, then the hard-remove is
non-breaking.

Pure + injected: the caller passes an ``issue(tenant_id, kind, config) -> credential_id``
callable wrapping the vault's issue API (Part A; dormant until that endpoint exists). The
function never talks to the network or DB itself — a thin script feeds it the tenant's
workflows and persists the rewritten ones. ``dry_run=True`` reports what *would* migrate
without issuing or mutating, and the rewrite is **idempotent**: a node that already has a
``credential_ref`` is skipped, so re-running is safe.

ponytail: inline secrets are detected by their (now-removed) field names — unambiguous
across the four credential-bearing workers. If a future worker reuses one of these field
names for a non-secret, add a worker_type guard here.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

_REF_KEY = "credential_ref"

# Holders a node can stash design-time values in (same trio as the StartRun resolver).
_HOLDERS = ("parameters", "config", "input_binding")


class _Issuer(Protocol):
    def __call__(self, *, tenant_id: str, kind: str, config: dict[str, Any]) -> str: ...


def _merged_values(node: Any) -> dict[str, Any]:
    """Flatten a node's parameters/config/input_binding into one read-only view."""
    merged: dict[str, Any] = {}
    for name in _HOLDERS:
        holder = getattr(node, name, None)
        if isinstance(holder, dict):
            merged.update(holder)
    return merged


def _existing_ref(node: Any) -> str | None:
    for name in _HOLDERS:
        holder = getattr(node, name, None)
        if isinstance(holder, dict):
            ref = holder.get(_REF_KEY)
            if isinstance(ref, str) and ref:
                return ref
    return None


def _detect_inline_secret(v: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]] | None:
    """Return ``(kind, secret_config, fields_to_strip)`` for a node's inline secret.

    ``kind`` is the worker auth type; ``secret_config`` is the ``{type, config}`` config
    slot the vault stores; ``fields_to_strip`` are the plaintext keys to delete. Note
    ``auth_type`` is a non-secret hint the http worker still reads — it is never stripped.
    """
    auth_type = v.get("auth_type")
    if v.get("auth_token") and auth_type in ("bearer", "api_key"):
        slot = "token" if auth_type == "bearer" else "value"
        config: dict[str, Any] = {slot: v["auth_token"]}
        if auth_type == "api_key" and v.get("api_key_header"):
            config["header"] = v["api_key_header"]
        return auth_type, config, ["auth_token", "api_key_header"]
    if auth_type == "basic" and v.get("auth_username"):
        return "basic", {
            "username": v["auth_username"],
            "password": v.get("auth_password", ""),
        }, ["auth_username", "auth_password"]
    if v.get("api_key"):  # agent-worker
        return "api_key", {"value": v["api_key"]}, ["api_key"]
    if v.get("webhook_url"):  # ms-teams-worker
        return "webhook_url", {"url": v["webhook_url"]}, ["webhook_url"]
    if v.get("username") and v.get("password"):  # database-connection-worker
        return "basic", {
            "username": v["username"],
            "password": v["password"],
        }, ["username", "password"]
    return None


def _rewrite_node(node: Any, credential_ref: str, strip: list[str]) -> None:
    """Set the credential_ref on the node and delete the inline secret plaintext."""
    params = getattr(node, "parameters", None)
    if not isinstance(params, dict):
        params = {}
        setattr(node, "parameters", params)
    params[_REF_KEY] = credential_ref
    config = getattr(node, "config", None)
    if isinstance(config, dict):
        config[_REF_KEY] = credential_ref
    for name in _HOLDERS:
        holder = getattr(node, name, None)
        if isinstance(holder, dict):
            for key in strip:
                holder.pop(key, None)


def migrate_workflow_inline_credentials(
    workflow: Any,
    *,
    tenant_id: str,
    issue: _Issuer,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Migrate one workflow's inline node secrets to vault credential_refs.

    Returns one record per migrated node (``{node_id, kind, credential_ref}`` or, in
    dry-run, ``{node_id, kind, dry_run: True}``). Nodes that already carry a
    ``credential_ref`` or hold no inline secret are skipped.
    """
    records: list[dict[str, Any]] = []
    for node in getattr(workflow, "nodes", []) or []:
        if _existing_ref(node):
            continue  # already migrated — idempotent
        detected = _detect_inline_secret(_merged_values(node))
        if detected is None:
            continue
        kind, secret_config, strip = detected
        node_id = getattr(node, "id", None)
        if dry_run:
            records.append({"node_id": node_id, "kind": kind, "dry_run": True})
            continue
        credential_ref = issue(tenant_id=tenant_id, kind=kind, config=secret_config)
        _rewrite_node(node, credential_ref, strip)
        records.append({"node_id": node_id, "kind": kind, "credential_ref": credential_ref})
    return records
