"""Shared credential coalesce for workers (Q1 — lifted from http-request-worker).

Every credential-bearing worker resolves its effective credential the same way: it
borrows a runtime credential from the run's encrypted ``__credentials`` map by the
node's ``credential_ref``, and if auth is required but nothing is found it fails closed.
This module is the single home of that logic so http / database / agent / teams workers
stop copy-pasting it.

Inline (author-supplied) secrets were removed in T5: ``credential_ref`` is now the only
way to supply a worker secret, so there is no inline-wins branch — the credential always
comes from the vault-populated ``__credentials`` map.

The returned credential is a self-describing ``{type, config}`` dict (or ``None``):
``bearer`` → ``config['token']``; ``api_key`` → ``config['value']`` (+ ``header``);
``basic`` → ``config['username'|'password']``; ``webhook_url`` → ``config['url']``.
"""

from __future__ import annotations

from typing import Any

# auth_type values that mean "no authentication".
_NO_AUTH = ("none", "")


def borrow_credential(inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``{type, config}`` entry the run lent this node, or ``None``.

    Reads ``inputs['__credentials'][credential_ref]`` where ``credential_ref`` falls
    back to ``"default"`` (preserving http-request-worker's original behaviour).
    """
    creds_map = inputs.get("__credentials")
    if isinstance(creds_map, dict):
        ref = inputs.get("credential_ref") or "default"
        cred = creds_map.get(ref)
        if isinstance(cred, dict) and cred.get("type"):
            return cred
    return None


def resolve_effective_auth(
    inputs: dict[str, Any],
    auth_type: str | None = None,
    require: bool = False,
) -> dict[str, Any] | None:
    """Coalesce a node's effective credential.

    **The credential is the source of truth.** If the run lent this node a credential
    (via ``credential_ref``), it decides *whether* and *how* to authenticate and it
    ALWAYS wins — ``auth_type`` never overrides or discards a supplied credential. This
    is the invariant that stops any worker from re-introducing the bug where a node has a
    credential selected but ``auth_type="none"`` (a common FE default) silently dropped it,
    so the request went out unauthenticated → ``401``.

    ``auth_type`` is only a fail-closed *hint* consulted when NO credential is present:

    * a credential is borrowable → return it (regardless of ``auth_type``);
    * otherwise, no-auth requested (``auth_type`` is ``None``/``none``/``""`` and not
      ``require``) → ``None``;
    * otherwise auth was required (``require=True`` or a non-empty, non-``none``
      ``auth_type``) but nothing was found → fail closed.
    """
    # Credential wins: a supplied credential decides auth on its own.
    borrowed = borrow_credential(inputs)
    if borrowed is not None:
        return borrowed
    # No credential borrowed. Honor an explicit/implicit no-auth request.
    if not require and (auth_type is None or auth_type in _NO_AUTH):
        return None
    # Auth was required but no credential was found → fail closed.
    raise ValueError(
        "authentication is required but no credential was found: set a "
        "credential_ref on the node and supply it through the run's credentials."
    )
