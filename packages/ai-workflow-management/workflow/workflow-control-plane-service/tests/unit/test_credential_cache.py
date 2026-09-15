"""Per-run credential cache, bounded by the credential's own expiry (SA-2290 / B6).

B3 (SA-2287) moved credential resolution to the dispatch boundary, which turned
one vault call per RUN into one per DISPATCH. Correct, and a real increase in
vault load: a credential used by ten nodes was fetched ten times. This is the
other half — cache it for the run, and never past the point the vault would
stop vouching for it.

Two properties are being pinned, and they pull against each other:

* fewer calls — one per (root run, tenant, ref), shared by sub-workflows;
* bounded staleness — a revoked or expired credential must stop working, and
  the ceiling is whichever is sooner: the cache TTL or the credential's own
  ``expires_at``.

The real ``BoundedTTLCache`` is used with a hand-cranked clock rather than a
fake cache, because the trap this file exists to guard is a property of that
class: it reads ``ttl_seconds=0`` as *never expires*, so the obvious way to
say "do not cache this" is exactly the way to cache it forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.exceptions import CredentialResolveAuthError, ExpressionError
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.credentials import RunCredentialProvider
from app.layer4_frameworks.caching.bounded_ttl_cache import BoundedTTLCache

CRED_REF = "2823fa09-d348-4c7c-9a04-6397e4782ced"
TTL = 300.0


class _Clock:
    """Hand-cranked monotonic clock — no sleeps in a TTL test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeVault:
    def __init__(self, expires_at=None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self._expires_at = expires_at
        self.error = error

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.calls.append((tenant_id, credential_id, user_id))
        if self.error is not None:
            raise self.error
        return {
            "type": "bearer",
            "config": {"token": "s3cr3t"},
            "expires_at": self._expires_at,
        }


def _node(node_id: str = "n1", ref: str | None = CRED_REF) -> WorkflowNode:
    return WorkflowNode(
        id=node_id, name=node_id, node_type=NodeType.TASK,
        worker_type="http-request-worker",
        config={"credential_ref": ref} if ref else {},
    )


def _workflow() -> Workflow:
    return Workflow(id="wf", name="wf", nodes=[_node()], edges=[])


def _run(run_id: str = "run-1", *, root: str | None = None, metadata=None) -> WorkflowRun:
    run = WorkflowRun(
        id=run_id, workflow_id="wf", status=RunStatus.RUNNING, input_data={},
        context=RunContext(
            variables={"input": {}},
            topology=GraphTopology.from_workflow(_workflow()),
        ),
        tenant_id="system", metadata=dict(metadata or {}),
    )
    run.root_run_id = root or run_id
    return run


def _provider(vault, clock=None, *, cached=True, run_repo=None):
    cache = None
    if cached:
        # Anonymous on purpose: a NAMED cache enrols in the global SA-2254
        # budget registry, and a test has no business leaving itself there.
        cache = BoundedTTLCache(
            maxsize=64, ttl_seconds=TTL, clock=clock or _Clock(),
        )
    return RunCredentialProvider(
        vault_client=vault, cache=cache, cache_ttl_seconds=TTL if cached else 0.0,
        run_repo=run_repo,
    )


class TestOneCallPerRun:
    async def test_two_nodes_sharing_a_ref_cost_one_call(self):
        vault = FakeVault()
        provider = _provider(vault)
        run = _run()

        await provider.for_node(_node("a"), run)
        await provider.for_node(_node("b"), run)

        assert len(vault.calls) == 1

    async def test_a_sub_workflow_shares_its_root_s_entry(self):
        """The point of keying on root_run_id: a child run is a different run
        with a different id, but the same credential and the same identity."""
        vault = FakeVault()
        provider = _provider(vault)

        await provider.for_node(_node(), _run("root-1"))
        await provider.for_node(_node(), _run("child-1", root="root-1"))

        assert len(vault.calls) == 1

    async def test_a_different_run_does_not_reuse_the_entry(self):
        """"Per-run" is the security half of the design: a credential revoked
        between two runs must not be served to the second one from the first
        one's cache."""
        vault = FakeVault()
        provider = _provider(vault)

        await provider.for_node(_node(), _run("run-1"))
        await provider.for_node(_node(), _run("run-2"))

        assert len(vault.calls) == 2

    async def test_a_hit_skips_the_identity_lookup(self):
        """Identity is a pure function of the root run, so it is already
        implied by the cache key. Computing it before the lookup would keep
        paying a repo read on every hit — half of what the cache is for."""
        reads: list[str] = []

        class _Repo:
            async def aget_by_id(self, run_id):
                reads.append(run_id)
                return _run("root-1", metadata={"user_id": "u-42"})

        vault = FakeVault()
        provider = _provider(vault, run_repo=_Repo())
        child = _run("child-1", root="root-1")

        await provider.for_node(_node("a"), child)
        assert reads == ["root-1"], "the first call must resolve the identity"
        await provider.for_node(_node("b"), child)
        assert reads == ["root-1"], "a cache hit went back to the repo"
        assert vault.calls == [("system", CRED_REF, "u-42")]


class TestExpiryBoundsTheCache:
    async def test_the_entry_dies_at_the_cache_ttl(self):
        clock = _Clock()
        vault = FakeVault()
        provider = _provider(vault, clock)
        run = _run()

        await provider.for_node(_node(), run)
        clock.advance(TTL + 1)
        await provider.for_node(_node(), run)

        assert len(vault.calls) == 2, "a stale credential outlived the TTL"

    async def test_a_short_lived_credential_dies_at_its_own_expiry(self):
        """``expires_at`` sooner than the TTL wins. This is what "honour
        expires_at" means in practice: the cache never outlives the token."""
        clock = _Clock()
        soon = datetime.now(timezone.utc) + timedelta(seconds=30)
        vault = FakeVault(expires_at=soon)
        provider = _provider(vault, clock)
        run = _run()

        await provider.for_node(_node(), run)
        clock.advance(31)                      # past expiry, well inside TTL
        await provider.for_node(_node(), run)

        assert len(vault.calls) == 2, (
            "a credential was served after the vault said it had expired"
        )

    async def test_a_long_lived_credential_still_dies_at_the_ttl(self):
        """The bound runs both ways — an expires_at a year out must not extend
        the cache past the revocation window the TTL exists to enforce."""
        clock = _Clock()
        vault = FakeVault(
            expires_at=datetime.now(timezone.utc) + timedelta(days=365),
        )
        provider = _provider(vault, clock)
        run = _run()

        await provider.for_node(_node(), run)
        clock.advance(TTL + 1)
        await provider.for_node(_node(), run)

        assert len(vault.calls) == 2

    async def test_an_already_expired_credential_is_never_cached(self):
        """THE trap. ``BoundedTTLCache`` reads ttl_seconds=0 as *never
        expires*, so computing "0 seconds left" and passing it straight through
        would cache a dead token permanently — the exact opposite of the
        intent. The provider returns None ("do not cache") instead.

        It is still handed to the worker: the vault is the authority on its own
        tokens, and clock skew between this pod and the vault must not break a
        workflow that would have worked.
        """
        clock = _Clock()
        vault = FakeVault(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        provider = _provider(vault, clock)
        run = _run()

        first = await provider.for_node(_node(), run)
        assert first[CRED_REF]["config"]["token"] == "s3cr3t", "passed through"

        await provider.for_node(_node(), run)
        assert len(vault.calls) == 2, "an expired credential was cached"

        clock.advance(10_000)
        await provider.for_node(_node(), run)
        assert len(vault.calls) == 3, "it was cached FOREVER — the ttl=0 trap"

    async def test_an_unparseable_expiry_is_not_cached(self):
        vault = FakeVault(expires_at="not-a-timestamp")
        provider = _provider(vault)
        run = _run()

        await provider.for_node(_node(), run)
        await provider.for_node(_node(), run)

        assert len(vault.calls) == 2

    async def test_a_naive_timestamp_is_read_as_utc(self):
        """The vault client parses ISO-8601 into an aware datetime, but a
        different client (or a stub) may hand back a naive one. Comparing naive
        to aware raises TypeError, which would surface as a credential failure
        rather than a caching decision."""
        clock = _Clock()
        naive = (datetime.now(timezone.utc) + timedelta(seconds=30)).replace(tzinfo=None)
        provider = _provider(FakeVault(expires_at=naive), clock)

        result = await provider.for_node(_node(), _run())

        assert result[CRED_REF]["type"] == "bearer"


class TestFailuresAreNotCached:
    async def test_a_refusal_is_retried_on_the_next_node(self):
        """A verdict can change the moment the owner turns on the automation
        grant (SA-2279). Caching it would keep the run failing for the whole
        TTL after the operator had already fixed the cause."""
        vault = FakeVault(error=CredentialResolveAuthError("403"))
        provider = _provider(vault)
        run = _run()

        with pytest.raises(CredentialResolveAuthError):
            await provider.for_node(_node("a"), run)
        vault.error = None
        result = await provider.for_node(_node("b"), run)

        assert result[CRED_REF]["type"] == "bearer"
        assert len(vault.calls) == 2


class TestTheCacheIsOptional:
    async def test_without_a_cache_everything_still_resolves(self):
        """CREDENTIAL_CACHE_TTL_S=0 disables it. That is the pre-B6 behaviour:
        correct, just one call per dispatch."""
        vault = FakeVault()
        provider = _provider(vault, cached=False)
        run = _run()

        first = await provider.for_node(_node("a"), run)
        await provider.for_node(_node("b"), run)

        assert first[CRED_REF]["config"]["token"] == "s3cr3t"
        assert len(vault.calls) == 2

    async def test_mutating_what_a_node_got_cannot_poison_the_cache(self):
        """The map becomes ``resolved_input["__credentials"]``, which the engine
        and node-definition layers walk and rewrite. Handing out the cached
        object itself would let one node's rewrite change what every later node
        of the run authenticates with."""
        provider = _provider(FakeVault())
        run = _run()

        first = await provider.for_node(_node("a"), run)
        first[CRED_REF]["config"]["token"] = "tampered"
        first[CRED_REF]["type"] = "none"
        second = await provider.for_node(_node("b"), run)

        assert second[CRED_REF] == {"type": "bearer", "config": {"token": "s3cr3t"}}

    async def test_transport_extras_never_reach_the_worker_cached_or_not(self):
        """``expires_at`` governs the cache and must not leak into the map the
        worker reads — on the miss OR the hit."""
        vault = FakeVault(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        provider = _provider(vault)
        run = _run()

        miss = await provider.for_node(_node("a"), run)
        hit = await provider.for_node(_node("b"), run)

        assert set(miss[CRED_REF]) == {"type", "config"}
        assert set(hit[CRED_REF]) == {"type", "config"}
