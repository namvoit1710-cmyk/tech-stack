"""Verify MAX_WORKER_THREADS default + env override.

The SDK sets ``loop.set_default_executor(ThreadPoolExecutor(MAX_WORKER_THREADS))``
at startup so ``asyncio.to_thread`` doesn't unbounded-spawn threads on
memory-constrained worker pods.
"""

from __future__ import annotations


def test_max_worker_threads_default_is_8_for_256mb_pod(monkeypatch) -> None:
    """Default 8 is sized for 256 MB worker pods (see config docstring)."""
    monkeypatch.delenv("MAX_WORKER_THREADS", raising=False)
    from worker_sdk.layer4_frameworks.config.app_config import Settings
    fresh = Settings()
    assert fresh.MAX_WORKER_THREADS == 8, (
        "MAX_WORKER_THREADS default changed; update the memory math"
    )


def test_max_worker_threads_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_WORKER_THREADS", "16")
    from worker_sdk.layer4_frameworks.config.app_config import Settings
    assert Settings().MAX_WORKER_THREADS == 16
