"""Verify MAX_CONCURRENT_TASKS env var flows through to ConcurrencyControl.

Replaces the hard-coded ``ConcurrencyControl()`` ceiling of 10. The
setting is read once at bootstrap time and applied to the singleton
``ConcurrencyControl`` instance shared across use cases.
"""

from __future__ import annotations

from app.layer2_application.services.concurrency_control import ConcurrencyControl


def test_default_constructor_ceiling_is_10_for_back_compat() -> None:
    """``ConcurrencyControl()`` with no argument still defaults to 10.

    Bootstrap-level callers should pass ``max_concurrent=settings...`` so
    the env var is honoured. Direct callers (e.g. tests) get the historic
    default.
    """
    cc = ConcurrencyControl()
    assert cc.max_concurrent == 10


def test_explicit_argument_overrides_default() -> None:
    cc = ConcurrencyControl(max_concurrent=50)
    assert cc.max_concurrent == 50

    # Accepts up to the cap, rejects beyond it.
    for _ in range(50):
        assert cc.acquire() is True
    assert cc.acquire() is False
    assert cc.active_count == 50

    cc.release()
    assert cc.acquire() is True


def test_settings_default_is_100_for_512mb_pod(monkeypatch) -> None:
    """The MAX_CONCURRENT_TASKS default is 100, sized for a 512 MB pod.

    Construct a fresh ``Settings`` instance instead of reloading the
    module — module reload pollutes other tests that import bootstrap.
    """
    monkeypatch.delenv("MAX_CONCURRENT_TASKS", raising=False)
    from app.layer4_frameworks.config.app_config import Settings
    fresh = Settings()
    assert fresh.MAX_CONCURRENT_TASKS == 100, (
        "Default MAX_CONCURRENT_TASKS changed; update the memory math"
    )


def test_settings_respects_env_override(monkeypatch) -> None:
    monkeypatch.setenv("MAX_CONCURRENT_TASKS", "200")
    from app.layer4_frameworks.config.app_config import Settings
    fresh = Settings()
    assert fresh.MAX_CONCURRENT_TASKS == 200


def test_acquire_never_exceeds_cap_under_thread_contention() -> None:
    """Smoke check (not a proof) that the counter is thread-safe.

    ConcurrencyControl is hit from the broker poll thread(s) AND FastAPI's
    sync-route threadpool. With an unlocked check-then-increment, two threads
    could both observe ``active_count < max`` and both increment past the cap.
    Hammer acquire/release from many threads and assert the cap is never
    breached and the count returns to 0. (Deterministic iteration count, not
    timing — fast and stable.)
    """
    import threading

    cc = ConcurrencyControl(max_concurrent=8)
    breaches: list[int] = []

    def worker() -> None:
        for _ in range(5000):
            if cc.acquire():
                seen = cc.active_count
                if seen > 8:
                    breaches.append(seen)
                cc.release()

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert breaches == []          # cap never breached
    assert cc.active_count == 0    # every acquire was released
