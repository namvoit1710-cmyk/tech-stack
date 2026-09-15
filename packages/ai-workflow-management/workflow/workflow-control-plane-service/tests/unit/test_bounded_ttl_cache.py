"""The shared cache primitive behind M3/M4 (SA-2254): allocate, then reclaim.

The central claim these tests defend is that a TTL alone does NOT bound
memory. If expiry is lazy and the key space is unbounded, every key that
arrives inside one TTL window is resident at once and nothing ever visits the
ones no one reads again. The LRU cap is the memory guarantee; the TTL is the
staleness guarantee. Both, always.
"""

from __future__ import annotations

import threading

import pytest

from app.layer4_frameworks.caching.bounded_ttl_cache import (
    MISS,
    BoundedTTLCache,
    env_int,
)


class _FakeClock:
    """A hand-cranked monotonic clock — no sleeping in tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTheMemoryGuarantee:
    def test_cap_holds_against_an_unbounded_key_space(self):
        """The exact scenario a TTL-only cache fails: 10k keys, one window."""
        clock = _FakeClock()
        cache = BoundedTTLCache(maxsize=100, ttl_seconds=900, clock=clock)
        for i in range(10_000):
            cache.put(f"run-{i}", {"i": i})
        assert cache.size() == 100, (
            f"{cache.size()} entries resident after 10k distinct keys inside "
            "one TTL window — this is why a TTL alone is not a memory bound"
        )
        assert cache.stats().evictions == 9_900

    def test_eviction_is_least_recently_USED_not_least_recently_written(self):
        cache = BoundedTTLCache(maxsize=3, ttl_seconds=0)
        for key in ("a", "b", "c"):
            cache.put(key, key)
        cache.get("a")           # 'a' becomes the most recent
        cache.put("d", "d")      # evicts 'b', the true LRU
        assert cache.get("a") == "a"
        assert cache.get("b") is MISS
        assert cache.get("c") == "c"
        assert cache.get("d") == "d"

    def test_maxsize_zero_disables_caching_entirely(self):
        """An operational kill switch that needs no code change."""
        cache = BoundedTTLCache(maxsize=0, ttl_seconds=60)
        cache.put("k", "v")
        assert cache.get("k") is MISS
        assert cache.size() == 0

    def test_overwriting_a_key_does_not_grow_the_cache(self):
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=0)
        for _ in range(1_000):
            cache.put("same", object())
        assert cache.size() == 1

    def test_clear_reclaims_everything(self):
        cache = BoundedTTLCache(maxsize=100, ttl_seconds=0)
        for i in range(100):
            cache.put(i, i)
        cache.clear()
        assert cache.size() == 0
        assert cache.get(0) is MISS


class TestTheStalenessGuarantee:
    def test_entry_expires_after_the_ttl(self):
        clock = _FakeClock()
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=30, clock=clock)
        cache.put("k", "v1")
        clock.advance(29)
        assert cache.get("k") == "v1"
        clock.advance(2)
        assert cache.get("k") is MISS
        assert cache.stats().expirations == 1

    def test_per_entry_ttl_override(self):
        """How a negative result gets a short life without a second cache."""
        clock = _FakeClock()
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=900, clock=clock)
        cache.put("found", "v")
        cache.put("missing", {}, ttl_seconds=10)
        clock.advance(11)
        assert cache.get("missing") is MISS, "the negative outlived its short TTL"
        assert cache.get("found") == "v", "the positive was expired by the override"

    def test_ttl_zero_means_no_expiry(self):
        clock = _FakeClock()
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=0, clock=clock)
        cache.put("k", "v")
        clock.advance(10_000_000)
        assert cache.get("k") == "v"

    def test_expired_entries_are_actually_released_not_just_hidden(self):
        clock = _FakeClock()
        cache = BoundedTTLCache(maxsize=1000, ttl_seconds=30, clock=clock)
        for i in range(500):
            cache.put(i, i)
        clock.advance(31)
        assert cache.purge_expired() == 500
        assert cache.size() == 0, "expired entries were hidden from reads but kept"

    def test_a_wall_clock_jump_cannot_mass_expire(self, monkeypatch):
        """Monotonic, not time.time — an NTP step must be a non-event."""
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=60)
        cache.put("k", "v")
        monkeypatch.setattr("time.time", lambda: 10 ** 12)
        assert cache.get("k") == "v"


class TestFalsyValuesAreRealValues:
    """The bug the MISS sentinel exists to make unrepresentable."""

    @pytest.mark.parametrize("value", [{}, [], "", 0, False, None])
    def test_a_falsy_cached_value_is_a_hit(self, value):
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=0)
        cache.put("k", value)
        assert cache.get("k") is not MISS
        assert cache.get("k") == value
        assert cache.stats().misses == 0

    def test_contains_agrees_with_get(self):
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=0)
        cache.put("empty", {})
        assert "empty" in cache
        assert "absent" not in cache


class TestTheCacheObjectItselfIsTruthy:
    """Regression: an EMPTY cache must not read as 'no cache'.

    ``__len__`` alone makes an empty cache falsy, so the idiomatic
    ``self._cache = injected or BoundedTTLCache(...)`` quietly throws the
    injected cache away and builds a default one — which is how a test that
    injects a fake clock ends up asserting against the real clock.
    """

    def test_empty_cache_is_truthy(self):
        assert bool(BoundedTTLCache(maxsize=10, ttl_seconds=0)) is True

    def test_the_or_idiom_keeps_an_injected_empty_cache(self):
        injected = BoundedTTLCache(maxsize=7, ttl_seconds=0, name="injected")
        chosen = injected or BoundedTTLCache(maxsize=999, ttl_seconds=0)
        assert chosen.stats().maxsize == 7

    def test_len_still_reports_occupancy(self):
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=0)
        assert len(cache) == 0
        cache.put("k", "v")
        assert len(cache) == 1


class TestObservability:
    def test_stats_track_the_budget_bucket(self):
        cache = BoundedTTLCache(maxsize=2, ttl_seconds=0, name="demo")
        cache.put("a", 1)
        cache.get("a")
        cache.get("nope")
        cache.put("b", 2)
        cache.put("c", 3)          # evicts 'a'
        s = cache.stats()
        assert s.name == "demo"
        assert s.size == 2 and s.maxsize == 2
        assert s.hits == 1 and s.misses == 1 and s.evictions == 1
        assert s.hit_rate == 0.5

    def test_hit_rate_is_zero_with_no_traffic(self):
        assert BoundedTTLCache(maxsize=1, ttl_seconds=0).stats().hit_rate == 0.0

    def test_keys_reports_only_live_entries(self):
        clock = _FakeClock()
        cache = BoundedTTLCache(maxsize=10, ttl_seconds=30, clock=clock)
        cache.put("old", 1)
        clock.advance(31)
        cache.put("new", 2)
        assert cache.keys() == ["new"]


class TestConcurrency:
    def test_concurrent_writers_never_exceed_the_cap(self):
        cache = BoundedTTLCache(maxsize=50, ttl_seconds=0)

        def hammer(offset: int) -> None:
            for i in range(2_000):
                cache.put(f"k-{offset}-{i}", i)
                cache.get(f"k-{offset}-{i // 2}")

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert cache.size() <= 50, (
            f"{cache.size()} entries under 8 concurrent writers — the cap is racy"
        )


class TestEnvInt:
    def test_reads_and_clamps(self, monkeypatch):
        monkeypatch.setenv("SA2254_X", "7")
        assert env_int("SA2254_X", 1) == 7
        assert env_int("SA2254_X", 1, minimum=99) == 99

    def test_garbage_does_not_stop_the_pod_booting(self, monkeypatch):
        monkeypatch.setenv("SA2254_X", "twelve")
        assert env_int("SA2254_X", 42) == 42

    def test_unset_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("SA2254_X", raising=False)
        assert env_int("SA2254_X", 42) == 42

    def test_empty_string_uses_the_default(self, monkeypatch):
        monkeypatch.setenv("SA2254_X", "")
        assert env_int("SA2254_X", 42) == 42
