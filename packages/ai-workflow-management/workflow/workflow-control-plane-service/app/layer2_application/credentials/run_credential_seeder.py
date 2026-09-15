"""Seal author-supplied credentials onto a run at start.

This used to do the vault work too: walk every node, resolve every
``credential_ref``, merge, encrypt, store. SA-2287 (B3) moved all of that to
the egress boundary — see :class:`RunCredentialProvider`. What is left here is
the half that genuinely belongs at start time: the caller of ``POST /runs`` may
hand us ``runtime_credentials`` directly, and there is nothing to defer about a
value we already hold.

So after B3, ``run.context.variables["__credentials"]`` means **"what the
caller supplied for this run"**, not "every secret this workflow might need".

Two consequences worth stating, because they are the reason this class did not
simply get deleted:

* Author-supplied entries still WIN over a vault entry of the same name. The
  provider merges them on top at dispatch. A caller who names a credential
  inline meant that one.
* A run started BEFORE B3 carries a fully-resolved map under the same key. The
  provider reads it as overrides, so those runs keep dispatching exactly as
  they did. Deploying B3 does not strand an in-flight run.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

#: Reserved run-variable key holding the (normally encrypted) credential map.
#: Never publish it — every emitter that ships ``variables`` or ``resolved_input``
#: filters this key out. See ``OrchestrationEngine._emit_node_input_for_executor``.
CREDENTIALS_KEY = "__credentials"


class RunCredentialSeeder:
    """Stores the caller's ``runtime_credentials`` on the run.

    ``cipher`` is optional and degrades exactly as it did before: without one
    the map is stored in plaintext with a warning, which is the dev/test path.
    Both shapes are readable by ``RunCredentialProvider.overrides_of``.
    """

    def __init__(self, cipher: Any = None, **_legacy: Any) -> None:
        # ``**_legacy`` swallows the pre-B3 ``vault_client=`` keyword so a
        # caller that still passes it gets seeding without vault resolution
        # rather than a TypeError at construction.
        self._cipher = cipher

    def seal(self, run: Any, credentials: dict[str, dict]) -> None:
        """Store ``credentials`` on ``run`` under the reserved key.

        No-op for an empty map, so a run that needs nothing keeps the key
        absent entirely — several tests assert exactly that, and an empty
        ``__credentials`` would read as "resolved to nothing" rather than
        "never asked".
        """
        if not credentials:
            return
        if self._cipher is not None:
            run.context.variables[CREDENTIALS_KEY] = self._cipher.encrypt(credentials)
            return
        # No cipher configured — store plaintext with a warning.
        # Set GATEWAY_REGISTRY_KEK_V1 env var for at-rest encryption.
        _logger.warning(
            "runtime_credentials stored WITHOUT encryption — "
            "GATEWAY_REGISTRY_KEK_V1 is not set. Do not use in production."
        )
        run.context.variables[CREDENTIALS_KEY] = credentials

    async def seed(
        self,
        run: Any,
        workflow: Any = None,
        *,
        tenant_id: str = "",
        user_id: str | None = None,
        overrides: dict[str, dict] | None = None,
    ) -> dict[str, dict]:
        """Seal the caller's entries; returns the effective map.

        Still ``async`` and still accepts ``workflow`` / ``tenant_id`` /
        ``user_id`` so the call sites did not have to be rewritten in the same
        change that moved the resolution. They are unused now — a node's
        credential is fetched when that node dispatches, against the tenant and
        identity read from the run at that moment.
        """
        effective = dict(overrides or {})
        self.seal(run, effective)
        return effective
