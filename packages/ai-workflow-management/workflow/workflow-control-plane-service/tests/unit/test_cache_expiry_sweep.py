"""Residency, not just occupancy (SA-2368).

``test_bounded_ttl_cache`` already defends the claim that a TTL alone does not
bound MEMORY — the LRU cap does that. What nothing defended is the other half:
how LONG a value stays resident. Expiry was lazy and nothing swept, so an entry
whose key is never read again was never visited, and a 300s TTL turned into a
residency of however long it takes ``maxsize`` later entries to arrive.

Measured on tenant-1 2026-08-12: the ``run_credentials`` bucket held 74 entries
with ``evictions=0`` after 13.5h of uptime — every credential decrypted since
boot still in memory, ~3.9 days against a 300s TTL, holding plaintext
``{type, config}`` from the vault.

Two independent mechanisms close it, and both are tested here because each
covers a case the other cannot:

* ``RunScopedTTLCache.evict_run`` — immediate, on the terminal transition.
  Does nothing for a run that never reaches a terminal state.
* the sweep — periodic, bounded by TTL + interval. Collects exactly those runs.
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from app.layer2_application.interfaces.run_scoped_cache_registry import (
    RunScopedCacheRegistry,
)
from app.layer4_frameworks.caching.bounded_ttl_cache import (
    MISS,
    BoundedTTLCache,
    RunScopedTTLCache,
    purge_all_expired,
    sweepable_names,
)
from app.layer4_frameworks.caching.cache_expiry_janitor import (
    PURGED_COUNTER,
    SWEEPS_COUNTER,
    CacheExpiryJanitor,
)


class _FakeClock:
    """A hand-cranked monotonic clock — no sleeping in tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _RecordingMonitor:
    """Captures increments so label KEYS can be asserted, not just values."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def increment(self, metric, tags=None, amount=1):
        self.calls.append((metric, tags, amount))

    def track(self, metric, value):
        pass

    def histogram(self, metric, value, tags=None):
        pass


def _cred_cache(name, clock=None, ttl=300.0, maxsize=512):
    """A cache shaped exactly like the credential one: (root, tenant, ref)."""
    return RunScopedTTLCache(
        maxsize=maxsize,
        ttl_seconds=ttl,
        name=name,
        clock=clock or _FakeClock(),
        run_id_of_key=lambda key: key[0],
    )


# ─────────────────────────── terminal eviction ───────────────────────────


class TestATerminalRunDropsItsSecrets:
    def test_only_the_terminating_runs_entries_go(self):
        cache = _cred_cache("t_only_its_own")
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        cache.put(("run-A", "tenant-1", "sap"), {"type": "oauth"})
        cache.put(("run-B", "tenant-1", "sf"), {"type": "basic"})

        assert cache.evict_run("run-A") == 2
        assert cache.size() == 1
        assert cache.get(("run-B", "tenant-1", "sf")) is not MISS
        assert cache.get(("run-A", "tenant-1", "sf")) is MISS
        # counted per ENTRY, not per call — two secrets left, so two.
        assert cache.budget_snapshot()["run_evictions"] == 2

    def test_a_child_terminating_does_not_disarm_the_running_root(self):
        """The key holds the ROOT id on purpose.

        A sub-workflow reaching COMPLETED must not pull the credential out from
        under the root run that is still dispatching nodes with it. Because the
        entry is keyed by root, the child's own id simply does not match.
        """
        cache = _cred_cache("t_child_vs_root")
        cache.put(("root-1", "tenant-1", "sf"), {"type": "basic"})

        assert cache.evict_run("child-9") == 0
        assert cache.get(("root-1", "tenant-1", "sf")) is not MISS

        # …and when the ROOT terminates, the entry a child created goes too.
        assert cache.evict_run("root-1") == 1

    def test_evicting_twice_is_a_no_op(self):
        """Terminal transitions fire more than once."""
        cache = _cred_cache("t_idempotent")
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        assert cache.evict_run("run-A") == 1
        assert cache.evict_run("run-A") == 0

    def test_an_unknown_or_empty_run_changes_nothing(self):
        cache = _cred_cache("t_unknown")
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        assert cache.evict_run("never-cached") == 0
        assert cache.evict_run("") == 0
        assert cache.size() == 1

    def test_an_empty_run_id_is_refused_even_when_a_key_carries_one(self):
        """A blank id is missing data, not a selector.

        Without the explicit guard an empty ``run_id`` would match any entry
        whose key carries a blank root — a malformed run quietly sweeping real
        secrets. It must decline instead.
        """
        cache = _cred_cache("t_blank_key")
        cache.put(("", "tenant-1", "sf"), {"type": "basic"})
        assert cache.evict_run("") == 0
        assert cache.size() == 1

    def test_run_evictions_is_visible_in_the_budget_export(self):
        """Without this counter there is no way to tell in production that the
        fix is firing — ``entries`` alone cannot distinguish "evicted" from
        "never cached"."""
        cache = _cred_cache("t_counter")
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        cache.evict_run("run-A")

        snap = cache.budget_snapshot()
        assert snap["run_evictions"] == 1
        assert snap["run_evict_errors"] == 0
        # the base fields survive the override
        assert snap["entries"] == 0
        assert snap["maxsize"] == 512


class TestAKeyTheMapperCannotRead:
    def test_a_bad_key_is_counted_rather_than_silently_skipped(self):
        """A skip here means a retained secret, so it has to become a number."""
        cache = RunScopedTTLCache(
            maxsize=8, ttl_seconds=300.0, name="t_bad_key",
            clock=_FakeClock(),
            run_id_of_key=lambda key: key[0],
        )
        # An int key is not subscriptable, so the mapper raises on it. (A str
        # would NOT do — ``"abc"[0]`` is legal and simply fails to match.)
        cache.put(42, {"type": "y"})
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})

        assert cache.evict_run("run-A") == 1
        assert cache.budget_snapshot()["run_evict_errors"] >= 1

    def test_a_bad_key_does_not_block_the_good_ones(self):
        cache = RunScopedTTLCache(
            maxsize=8, ttl_seconds=300.0, name="t_bad_key_isolated",
            clock=_FakeClock(), run_id_of_key=lambda key: key[0],
        )
        cache.put(42, {"type": "unreadable"})
        cache.put(("run-A", "tenant-1", "a"), {"type": "basic"})
        cache.put(("run-A", "tenant-1", "b"), {"type": "basic"})

        assert cache.evict_run("run-A") == 2


class TestTheRegistryGateStaysMeaningful:
    def test_a_plain_bounded_cache_still_cannot_join(self):
        """``evict_run`` lives on the subclass so this check keeps discriminating.

        Had it gone on the base, ``hasattr(cache, "evict_run")`` would be true
        for every bucket in the service — including the six whose keys are not
        runs — and the registry's guard would always pass.
        """
        registry = RunScopedCacheRegistry()
        plain = BoundedTTLCache(maxsize=4, ttl_seconds=1.0, name="t_plain")
        with pytest.raises(TypeError, match="evict_run"):
            registry.register(plain)

    def test_a_run_scoped_cache_joins_and_release_run_reaches_it(self):
        registry = RunScopedCacheRegistry()
        cache = RunScopedTTLCache(
            maxsize=8, ttl_seconds=300.0, name="t_joins",
            clock=_FakeClock(), run_id_of_key=lambda key: key[0],
            run_scoped_registry=registry,
        )
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})

        assert registry.release_run("run-A") == 1
        assert cache.size() == 0


# ────────────────────────────── the sweep ────────────────────────────────


class TestTheSweep:
    def test_an_entry_nobody_reads_again_is_still_collected(self):
        """THE regression test.

        This is the exact shape measured in production: put once, never read
        again, TTL long past. Before the sweep existed the entry stayed
        resident until the LRU cap evicted it.
        """
        clock = _FakeClock()
        cache = _cred_cache("t_never_read_again", clock=clock, ttl=300.0)
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})

        clock.advance(3600)  # an hour past a five-minute TTL

        # Nothing has read the key, so lazy expiry has never seen it: the value
        # is still in memory. This assertion is the bug.
        assert cache.size() == 1

        purge_all_expired()
        assert cache.size() == 0

    def test_the_sweep_reports_what_it_dropped_per_bucket(self):
        clock = _FakeClock()
        cache = _cred_cache("t_reported", clock=clock, ttl=10.0)
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        cache.put(("run-B", "tenant-1", "sf"), {"type": "basic"})
        clock.advance(60)

        dropped = purge_all_expired()
        assert dropped.get("t_reported") == 2

    def test_a_live_entry_survives_the_sweep(self):
        clock = _FakeClock()
        cache = _cred_cache("t_live_survives", clock=clock, ttl=300.0)
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        clock.advance(30)

        dropped = purge_all_expired()
        assert cache.get(("run-A", "tenant-1", "sf")) is not MISS
        # a bucket that dropped nothing is ABSENT, not present with a 0 —
        # otherwise every idle sweep emits a sample per bucket forever.
        assert "t_live_survives" not in dropped

    def test_an_anonymous_cache_does_not_join_the_sweep(self):
        """Same gate as the budget registry: unnamed means throwaway.

        The reference is BOUND deliberately. Membership is weak, so an
        unreferenced cache is collected immediately and the assertion would
        pass whether or not the gate exists — the test would prove nothing.
        """
        anonymous = BoundedTTLCache(maxsize=4, ttl_seconds=1.0)  # no name
        anonymous.put("k", "v")
        assert "" not in sweepable_names()
        assert anonymous.size() == 1  # still alive at the point of assertion

    def test_one_failing_cache_does_not_cost_the_others_their_sweep(self):
        class _Broken(BoundedTTLCache):
            def purge_expired(self):
                raise RuntimeError("this bucket is having a bad day")

        clock = _FakeClock()
        broken = _Broken(maxsize=4, ttl_seconds=1.0, name="t_broken")
        broken.put("k", "v")
        healthy = _cred_cache("t_healthy_neighbour", clock=clock, ttl=10.0)
        healthy.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        clock.advance(60)

        dropped = purge_all_expired()
        assert dropped.get("t_healthy_neighbour") == 1
        assert "t_broken" not in dropped

    def test_membership_is_weak(self):
        """A sweeper that pinned every cache would be a leak added by the fix."""
        cache = BoundedTTLCache(
            maxsize=4, ttl_seconds=1.0, name="t_weakly_held",
        )
        assert "t_weakly_held" in sweepable_names()
        del cache
        gc.collect()
        assert "t_weakly_held" not in sweepable_names()


# ───────────────────────────── the janitor ───────────────────────────────


class TestTheJanitor:
    def test_a_sweep_emits_the_same_label_keys_every_time(self):
        """prometheus_client freezes a family's label set on first observation
        and drops mismatched samples SILENTLY, so this is asserted rather than
        assumed."""
        clock = _FakeClock()
        cache = _cred_cache("t_labels", clock=clock, ttl=10.0)
        monitor = _RecordingMonitor()
        janitor = CacheExpiryJanitor(monitor, interval_s=60)

        for run in ("run-A", "run-B", "run-C"):
            cache.put((run, "tenant-1", "sf"), {"type": "basic"})
            clock.advance(60)
            janitor.sweep_once()

        purge_calls = [c for c in monitor.calls if c[0] == PURGED_COUNTER]
        assert purge_calls, "the janitor never reported a purge"
        assert {frozenset((c[1] or {}).keys()) for c in purge_calls} == {
            frozenset({"bucket"})
        }

    def test_the_sweep_counter_separates_idle_from_dead(self):
        """A permanent zero on the purge counter is ambiguous on its own."""
        monitor = _RecordingMonitor()
        janitor = CacheExpiryJanitor(monitor, interval_s=60)
        janitor.sweep_once()

        assert [c for c in monitor.calls if c[0] == SWEEPS_COUNTER]
        assert janitor.snapshot()["sweeps"] == 1

    def test_a_failing_sweep_does_not_escape(self, monkeypatch):
        monkeypatch.setattr(
            "app.layer4_frameworks.caching.cache_expiry_janitor"
            ".purge_all_expired",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        janitor = CacheExpiryJanitor(_RecordingMonitor(), interval_s=60)

        assert janitor.sweep_once() == {}
        assert janitor.snapshot()["errors"] == 1

    def test_a_broken_monitor_cannot_break_the_sweep(self):
        class _BadMonitor:
            def increment(self, *a, **k):
                raise RuntimeError("metrics backend is down")

        clock = _FakeClock()
        cache = _cred_cache("t_bad_monitor", clock=clock, ttl=10.0)
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        clock.advance(60)

        janitor = CacheExpiryJanitor(_BadMonitor(), interval_s=60)
        assert janitor.sweep_once().get("t_bad_monitor") == 1
        assert cache.size() == 0

    def test_a_sub_second_interval_is_floored_rather_than_spinning(self):
        assert CacheExpiryJanitor(None, interval_s=0).snapshot()["interval_s"] == 1.0
        assert CacheExpiryJanitor(None, interval_s=-5).snapshot()["interval_s"] == 1.0

    @pytest.mark.asyncio
    async def test_run_forever_stops_when_asked(self):
        janitor = CacheExpiryJanitor(_RecordingMonitor(), interval_s=1)
        task = asyncio.create_task(janitor.run_forever())
        await asyncio.sleep(0)
        janitor.stop()
        await asyncio.wait_for(task, timeout=5)
        assert task.done()

    @pytest.mark.asyncio
    async def test_the_loop_actually_sweeps_on_its_interval(self):
        clock = _FakeClock()
        cache = _cred_cache("t_loop_sweeps", clock=clock, ttl=10.0)
        cache.put(("run-A", "tenant-1", "sf"), {"type": "basic"})
        clock.advance(60)

        janitor = CacheExpiryJanitor(_RecordingMonitor(), interval_s=1)
        task = asyncio.create_task(janitor.run_forever())
        for _ in range(50):
            await asyncio.sleep(0.05)
            if cache.size() == 0:
                break
        janitor.stop()
        task.cancel()

        assert cache.size() == 0, "the loop never ran a sweep"
