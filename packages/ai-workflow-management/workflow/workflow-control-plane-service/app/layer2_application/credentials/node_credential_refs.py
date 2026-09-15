"""Producer step — resolve node ``credential_ref``s against the vault (Q3 = REST).

Walk a workflow's nodes, collect every node's reserved ``credential_ref``, resolve
each against the vault (:class:`VaultCredentialClient`), and return a
``{ref: {type, config}}`` map. Everything downstream — encryption, dispatch, the
per-worker coalesce — is unchanged: the worker reads
``inputs["__credentials"][credential_ref]`` exactly as http-request-worker does.

Tenant is always the run's tenant (``TENANT_BLEED``); the vault enforces it
server-side.

Lives here rather than under ``features/start_run/`` because it now has two
callers — ``StartRunUseCase`` for a top-level run and ``ChildWorkflowCoordinator``
for a sub-workflow's child run — and a shared collaborator that imports a feature
package is a cycle waiting to happen. It was one: ``features/start_run/__init__``
imports the use case, which imports this package. The old module path re-exports
these names, so existing imports keep working.
"""

from __future__ import annotations

from typing import Any, Protocol


class _Resolver(Protocol):
    async def resolve_by_id(
        self, tenant_id: str, credential_id: str, user_id: str | None = None
    ) -> dict[str, Any]: ...


_REF_KEY = "credential_ref"


def credential_ref_of(node: Any) -> str | None:
    """Pull a node's credential_ref from wherever the FE stored it.

    The picker writes a static credential id; depending on the API shape it lands in
    ``config`` (engine-internal), ``parameters`` (FE-facing), or ``input_binding``.
    """
    for holder in (
        getattr(node, "config", None),
        getattr(node, "parameters", None),
        getattr(node, "input_binding", None),
    ):
        if isinstance(holder, dict):
            ref = holder.get(_REF_KEY)
            if isinstance(ref, str) and ref:
                return ref
    return None


#: Pre-B3 name. ``RunCredentialProvider`` asks per node, so this stopped being
#: an internal detail of the collect-everything walk and got a public name.
_node_credential_ref = credential_ref_of


def collect_refs(workflow: Any) -> list[str]:
    """Every distinct ``credential_ref`` declared by ``workflow``'s own nodes.

    Split out of :func:`collect_and_resolve` so a caller can ask the cheap
    question — *does this workflow need the vault at all?* — before paying for
    anything. ``ChildWorkflowCoordinator`` asks it on every sub-workflow start:
    an empty list means no vault call, and no lookup of who the run's initiator
    was either.

    Own nodes only. A WORKFLOW node names its child by id; following that id
    needs a repository this function does not have, which is why a
    sub-workflow's refs are resolved when the CHILD run starts rather than
    being hoisted into its parent's map.
    """
    refs: list[str] = []
    for node in getattr(workflow, "nodes", []) or []:
        ref = credential_ref_of(node)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


async def collect_and_resolve(
    workflow: Any, tenant_id: str, user_id: str | None, client: _Resolver
) -> dict[str, dict]:
    """Resolve every distinct node credential_ref to a ``{type, config}`` entry."""
    refs = collect_refs(workflow)

    resolved: dict[str, dict] = {}
    for ref in refs:
        entry = await client.resolve_by_id(tenant_id, ref, user_id)
        # Only {type, config} belong in __credentials — drop transport extras
        # (expires_at) so the encrypted map stays the shape workers expect.
        resolved[ref] = {"type": entry["type"], "config": entry["config"]}
    return resolved
