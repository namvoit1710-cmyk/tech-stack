"""Resolve a run's credentials at the EGRESS boundary, on demand (SA-2287 / B3).

Invariant 4 of the saga model (SA-2285): secrets are resolved where they are
used, with the service's own authority, and never sit around waiting.

What moved
----------
Before, ``StartRunUseCase`` walked EVERY node of the workflow, resolved EVERY
``credential_ref`` against the vault, and sealed the whole map into
``run.context.variables["__credentials"]`` before the first node could run.
``ChildWorkflowCoordinator`` did the same for a sub-workflow's child run.

Now nothing is resolved at start. Each node asks this provider for its OWN
``credential_ref`` at dispatch, and the answer is used and discarded.

What that buys, in the order it matters:

* A vault refusal can no longer stop a run from existing. It lands on the node
  that needed the credential, red, with the reason — the engine's existing
  ``ExpressionError`` branch in ``_exec_uniform`` already does exactly that.
  (This is most of SA-2294 falling out for free.)
* A 20-node workflow using one credential resolves one ref, not twenty, and
  resolves it when that node runs rather than before any node can start.
* A credential on a branch that is never taken is never fetched. Before, a
  broken credential on dead code failed the whole run at start.
* A long-paused run (INPUT node waiting on a human) dispatches with a secret
  fetched now, not one fetched hours ago.

What deliberately did NOT change
--------------------------------
* **The worker contract.** The control plane still hands the worker
  ``resolved_input["__credentials"]`` as ``{ref: {type, config}}``, and
  ``worker_sdk...effective_auth.borrow_credential`` still reads
  ``inputs["__credentials"][credential_ref or "default"]``. No worker changes.
* **Workflow definitions.** ``credential_ref`` still lives on the node exactly
  where the FE picker writes it. Nothing to migrate, nothing to re-author.
* **Who we resolve as.** Same rule as before — the identity of the ROOT run
  (``metadata["user_id"]``), so a sub-workflow resolves as whoever started the
  top-level run rather than silently becoming a user-less caller. Changing that
  rule to system authority is B4 (SA-2288) and is deliberately NOT done here.
* **Author-supplied entries win.** ``POST /runs`` with an explicit
  ``runtime_credentials`` is still sealed onto the run at start (there is
  nothing to defer — the caller already handed us the value) and still beats a
  vault entry of the same name.

One resolution path (B7 / SA-2291)
----------------------------------
Root runs and sub-workflow child runs both arrive here, through the same
``for_node``. There is no child-specific branch to drift: a child run resolves
its own node's ref, as the root's identity, by the same rules. The seeder that
used to duplicate the logic no longer resolves anything at all.

Narrowing note
--------------
A node is handed its OWN ref plus any author overrides — not the whole
workflow's map. That is safe against the worker contract because
``borrow_credential`` only ever looks up this node's ``credential_ref`` (or the
literal ``"default"``, which is why overrides are passed through whole). It is
also the difference between "this node can authenticate as itself" and "this
node holds every secret the workflow owns".
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.credentials.node_credential_refs import (
    credential_ref_of,
)
from app.layer2_application.credentials.run_credential_seeder import CREDENTIALS_KEY

_logger = logging.getLogger(__name__)

#: The ref ``worker_sdk.borrow_credential`` falls back to when a node declares
#: none — ``inputs["__credentials"][credential_ref or "default"]``. Named here
#: because the phase-0 instrumentation below is about exactly this fallback.
DEFAULT_CREDENTIAL_REF = "default"

#: Cache-miss sentinel. ``None`` is not usable — it is a legitimate stored
#: value elsewhere — and this keeps the provider from importing layer 4's.
_MISS = object()

#: A ``credential_ref`` that names its credential indirectly through an
#: environment variable — ``{{$env.API_TOKEN}}`` (SA-2365). The whole field is
#: the expression; the key matches env-variable key rules (letters/digits/_/-).
_ENV_REF_EXPR = re.compile(r"^\{\{\s*\$env\.([A-Za-z0-9_-]+)\s*\}\}$")


def _resolve_env_ref(ref: str | None, run: Any) -> str | None:
    """Turn a ``credential_ref`` of the form ``{{$env.KEY}}`` into the concrete
    credential id from the run's bound ``$env`` source.

    Switching environment then switches which credential a node uses without
    editing the node. The ``$env`` value is the credential REFERENCE id (a
    non-secret already carried in the run context, see
    ``Environment.non_secret_env_map``); the vault resolves the secret from that
    id at dispatch. A literal id passes through unchanged; an expression that
    resolves to nothing is left as-is so the vault reports a clear verdict rather
    than this silently swallowing it.
    """
    if not ref:
        return ref
    m = _ENV_REF_EXPR.match(ref)
    if not m:
        return ref
    env = (getattr(getattr(run, "context", None), "variables", None) or {}).get("env") or {}
    value = env.get(m.group(1))
    return value if isinstance(value, str) and value else ref


def _copy_entry(entry: dict) -> dict:
    """A private copy of a cached ``{type, config}`` entry.

    The map handed back becomes ``resolved_input["__credentials"]``, which the
    engine and node-definition layers are free to walk and rewrite. Returning
    the cached object itself would let any such rewrite reach into the cache
    and change what every LATER node of the run authenticates with. One shallow
    copy per level is enough — ``config`` holds scalars.
    """
    return {"type": entry.get("type"), "config": dict(entry.get("config") or {})}


class RunCredentialProvider:
    """On-demand ``{ref: {type, config}}`` for one node of one run.

    Every collaborator is optional and the provider degrades the way the code
    it replaces did:

    * no ``vault_client`` — the vault is dormant (``CREDENTIALS_VAULT_URL``
      unset). Author-supplied entries are still served.
    * no ``cipher`` — the sealed override map is read as plaintext, which is
      the dev/test path that ``RunCredentialSeeder.seal`` writes.
    * no ``run_repo`` — the identity walk stops at the run in hand. A top-level
      run is unaffected (it is its own root); a child run then resolves
      user-less, which is what it did before any of this existed.
    """

    def __init__(
        self,
        vault_client: Any = None,
        cipher: Any = None,
        run_repo: Any = None,
        cache: Any = None,
        cache_ttl_seconds: float = 0.0,
    ) -> None:
        self._vault = vault_client
        self._cipher = cipher
        self._run_repo = run_repo
        # SA-2290 (B6). Duck-typed on ``get(key, default)`` /
        # ``put(key, value, ttl_seconds=)`` — a ``BoundedTTLCache``, built and
        # named by bootstrap. It is INJECTED rather than constructed here
        # because that class lives in layer 4 and this module is layer 2: the
        # dependency runs one way in this codebase (131 layer4->layer2 imports,
        # zero the other way) and a cache is not worth inverting it for.
        #
        # ``None`` means no caching, which is the pre-B6 behaviour: correct,
        # just one vault call per dispatch.
        self._cache = cache
        # The cache's own default TTL, passed in rather than read off the
        # object so nothing here touches its private state. Used only to bound
        # a credential's own expiry — see :meth:`_cache_ttl_for`.
        self._cache_ttl = max(0.0, float(cache_ttl_seconds))

    @property
    def is_active(self) -> bool:
        """True when the vault can be asked at all."""
        return self._vault is not None

    # ------------------------------------------------------------------
    # The egress call
    # ------------------------------------------------------------------

    async def for_node(self, node: Any, run: Any) -> dict[str, dict]:
        """The credential map to hand THIS node.

        Raises the vault's verdicts (``CredentialNotFound`` /
        ``CredentialResolveAuthError``) rather than returning a partial map:
        dispatching a node with auth it did not ask for is worse than failing
        it. The caller decides how a verdict surfaces — ``TaskNodeHandler``
        turns it into an ``ExpressionError`` so it lands on the node.
        """
        overrides = self.overrides_of(run)
        ref = _resolve_env_ref(credential_ref_of(node), run)
        if not ref and DEFAULT_CREDENTIAL_REF in overrides:
            # PHASE 0 instrumentation for retiring the pass-through credential
            # (spec §13.6). This node declares NO credential_ref, and the run
            # carries a "default" entry — so worker_sdk's
            # ``borrow_credential(inputs["__credentials"][ref or "default"])``
            # is about to hand it that entry.
            #
            # This is the ONLY shape that would change behaviour SILENTLY if
            # ``runtime_credentials`` were removed. A node that declares a real
            # ``auth_type`` and finds nothing fails closed and loudly
            # (``resolve_effective_auth`` raises); a node with auth_type
            # none/"" that is quietly borrowing this would simply start going
            # out unauthenticated — the exact bug that function's docstring was
            # written about.
            #
            # 142 of 3,344 workflow definitions carry bearer/Authorization with
            # no credential_ref, and static analysis cannot say which of them
            # land here. Silence on this line over real traffic is the
            # precondition for phase 1.
            _logger.warning(
                "PASSTHROUGH_CREDENTIAL_BORROWED run=%s node=%s — node has no "
                "credential_ref and will borrow the run's 'default' entry",
                getattr(run, "id", ""), getattr(node, "id", ""),
            )
        if not ref or ref in overrides or not self.is_active:
            # Nothing to fetch: the node declares no ref, the caller already
            # supplied that exact one (author wins), or the vault is dormant.
            return overrides
        resolved = await self._resolve(ref, run)
        return {**resolved, **overrides}

    async def for_refs(self, refs: list[str], run: Any) -> dict[str, dict]:
        """Same, for an explicit ref list — the ``$credentials`` frame's entry
        point, where the consumer is an expression rather than a node."""
        overrides = self.overrides_of(run)
        if not self.is_active:
            return overrides
        resolved: dict[str, dict] = {}
        for ref in refs:
            if ref in overrides:
                continue
            resolved.update(await self._resolve(ref, run))
        return {**resolved, **overrides}

    async def _resolve(self, ref: str, run: Any) -> dict[str, dict]:
        """One vault round-trip, cached for the rest of this run (SA-2290 / B6).

        Keyed on the ROOT run id, which is what "per-run cache" means when a
        run has sub-workflows: a child shares its root's key, so a credential
        used by ten nodes across three nested workflows costs one call. Without
        this, B3's move to per-node resolution turned one call per RUN into one
        call per DISPATCH — correct, but a real increase in vault load.

        The lookup happens BEFORE the identity walk on purpose. Identity is a
        pure function of the root run, so it is already implied by the key and
        would be redundant in it — and computing it first would keep paying the
        repo read on every cache hit, which is half of what this exists to
        avoid.

        Failures are NOT cached. A verdict can change the moment the owner
        turns on the automation grant (SA-2279), and a negative entry would
        keep the run failing for the whole TTL after the operator has already
        fixed it.
        """
        key = (self._root_id(run), run.tenant_id, ref)
        if self._cache is not None:
            hit = self._cache.get(key, _MISS)
            if hit is not _MISS:
                return {ref: _copy_entry(hit)}

        user_id = await self._identity(run)
        entry = await self._vault.resolve_by_id(run.tenant_id, ref, user_id)
        # Only {type, config} belong in the map the worker reads — drop
        # transport extras so the shape stays exactly what
        # ``borrow_credential`` expects.
        value = {"type": entry["type"], "config": entry["config"]}

        if self._cache is not None:
            ttl = self._cache_ttl_for(entry.get("expires_at"), ref)
            if ttl is not None:
                # The cache keeps its OWN copy, never the object handed to the
                # caller. Storing the same dict would leave the very first node
                # of a run holding a live reference into the cache, so its
                # rewrites would decide what every later node authenticates
                # with — the read path's copy alone does not close that.
                self._cache.put(key, _copy_entry(value), ttl_seconds=ttl)
        return {ref: value}

    @staticmethod
    def _root_id(run: Any) -> str:
        return getattr(run, "root_run_id", "") or getattr(run, "id", "")

    def _cache_ttl_for(self, expires_at: Any, ref: str) -> float | None:
        """Seconds this credential may be cached, or ``None`` to not cache it.

        Honouring ``expires_at`` (the other half of B6) is really a statement
        about the CACHE: the vault decides whether a credential is usable right
        now, and it just said yes. What must not happen is this service holding
        that answer past the point the vault would change its mind. So the
        entry's life is bounded by its own expiry, never the other way round.

        A credential that is ALREADY expired is passed through to the worker
        (the vault is the authority on its own tokens, and clock skew between
        this pod and the vault must not break a working workflow) but is never
        cached, and says so in the log.

        ``None`` rather than ``0.0`` is the "do not cache" signal because
        ``BoundedTTLCache`` reads ``ttl_seconds=0`` as *never expires* — the
        one value that would turn a dead credential into a permanent one.
        """
        if expires_at is None:
            return self._cache_ttl or None
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                _logger.warning(
                    "credential %s has an unparseable expires_at (%r) — not cached",
                    ref, expires_at,
                )
                return None
        if not isinstance(expires_at, datetime):
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            _logger.warning(
                "the vault returned credential %s already expired (%s) — passing "
                "it to the worker unchanged, but not caching it",
                ref, expires_at.isoformat(),
            )
            return None
        if not self._cache_ttl:
            return remaining
        return min(self._cache_ttl, remaining)

    # ------------------------------------------------------------------
    # Author-supplied overrides, sealed on the run at start
    # ------------------------------------------------------------------

    def overrides_of(self, run: Any) -> dict[str, dict]:
        """Decrypt the run's sealed ``__credentials`` map.

        Post-B3 this holds only what the caller passed to ``POST /runs``. It
        also, on purpose, still reads whatever an OLDER run sealed: a run that
        started before this change carries its fully-resolved map, and because
        overrides win, that run keeps dispatching exactly as it did. In-flight
        runs do not break on deploy.

        Never raises — a map that cannot be decrypted degrades to empty and the
        worker fails closed, which is what the code this replaces did.
        """
        raw = (getattr(getattr(run, "context", None), "variables", None) or {}).get(
            CREDENTIALS_KEY
        )
        if raw is None:
            return {}
        try:
            from app.layer1_domain.value_objects.encrypted_credential import (
                EncryptedCredential,
            )

            if isinstance(raw, EncryptedCredential) and self._cipher is not None:
                return dict(self._cipher.decrypt(raw))
            if (
                isinstance(raw, dict)
                and "ciphertext_b64" in raw
                and self._cipher is not None
            ):
                # Deserialized path: the object was JSON-round-tripped to a dict.
                return dict(
                    self._cipher.decrypt(
                        EncryptedCredential(
                            ciphertext_b64=raw["ciphertext_b64"],
                            key_version=raw.get("key_version", 1),
                        )
                    )
                )
            if isinstance(raw, dict):
                # Plaintext fallback: no cipher was configured at seal time.
                return dict(raw)
        except Exception as exc:  # noqa: BLE001 - fail closed, never fail loud
            _logger.warning(
                "could not read the sealed credential map for run %s: %s — "
                "the worker will fail closed if it needs auth",
                getattr(run, "id", "?"),
                exc,
            )
        return {}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    async def _identity(self, run: Any) -> str | None:
        """The user the ROOT caller ran as.

        Unchanged from ``ChildWorkflowCoordinator._initiator_user_id``, which
        this replaces: a child run must resolve as the same identity the root
        did, or a sub-workflow of a hand-started run silently becomes a
        user-less caller and needs an automation grant its parent never needed.

        ``metadata`` is not propagated down the run chain on purpose — it also
        carries agent routing keys (``target_topic``, ``source``) that must not
        follow a child — so the identity is read from the root instead. A
        top-level run answers from memory; deeper, one repo read, and only when
        a ref actually has to be fetched.
        """
        own = (getattr(run, "metadata", None) or {}).get("user_id")
        root_id = getattr(run, "root_run_id", "")
        if own or not root_id or root_id == run.id or self._run_repo is None:
            return own
        root = await self._run_repo.aget_by_id(root_id)
        if root is None:
            return None
        return (getattr(root, "metadata", None) or {}).get("user_id")
