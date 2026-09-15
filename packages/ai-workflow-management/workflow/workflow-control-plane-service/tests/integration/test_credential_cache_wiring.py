"""The credential cache is only run-scoped if bootstrap actually builds it that way.

``RunScopedTTLCache`` is correct in isolation (``tests/unit/test_cache_expiry_sweep``)
and would still have changed nothing in production if bootstrap kept constructing
a plain ``BoundedTTLCache`` — which is exactly the shape of the original defect:
the eviction mechanism existed (``RunScopedCacheRegistry``, used by the projector
store since SA-2258) and the credential cache simply never joined it.

That failure mode leaves no trace. Runs still work, credentials still resolve,
``/metrics`` still serves ``wcp_cache_entries{bucket="run_credentials"}`` — the
number just keeps climbing with uptime. Only a test that builds the real
container can tell the two apart.

Lives in ``tests/integration`` because it builds the whole container (~15s).
"""

from bootstrap import build_app_container

from app.layer2_application.interfaces.run_scoped_cache_registry import (
    default_run_scoped_cache_registry,
)
from app.layer4_frameworks.caching.bounded_ttl_cache import (
    RunScopedTTLCache,
    sweepable_names,
)


def _credential_cache():
    container = build_app_container()
    provider = container.get("_credential_provider")
    assert provider is not None, "no credential provider in the container"
    return getattr(provider, "_cache", None)


def test_the_production_credential_cache_is_run_scoped():
    cache = _credential_cache()
    assert cache is not None, (
        "the credential cache is absent — CREDENTIAL_CACHE_TTL_S=0 would do "
        "this, and then nothing below is being exercised"
    )
    assert isinstance(cache, RunScopedTTLCache), (
        f"bootstrap built a {type(cache).__name__}; a plain BoundedTTLCache "
        "cannot join the run-scoped registry, so a terminal run would never "
        "release its decrypted credentials"
    )


def test_it_joined_the_run_scoped_registry():
    """Membership is what makes ``release_run`` reach it on a terminal run."""
    _credential_cache()
    assert "run_credentials" in default_run_scoped_cache_registry().members()


def test_it_joined_the_expiry_sweep():
    """The second, independent bound — the one that collects runs which never
    reach a terminal state at all (SA-1725: roughly a third of loops)."""
    _credential_cache()
    assert "run_credentials" in sweepable_names()


def test_a_terminal_run_releases_its_credentials_through_the_real_registry():
    """End to end through the same call the orchestration engine makes."""
    cache = _credential_cache()
    cache.put(("run-wiring-test", "tenant-1", "sf"), {"type": "basic",
                                                      "config": {}})
    before = cache.size()

    default_run_scoped_cache_registry().release_run("run-wiring-test")

    assert cache.size() == before - 1
    assert cache.budget_snapshot()["run_evictions"] >= 1
