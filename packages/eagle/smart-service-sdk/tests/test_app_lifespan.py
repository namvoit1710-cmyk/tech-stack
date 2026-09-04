import asyncio

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI

import smart_service_sdk


class _Coordinator:
    def __init__(self, events: list[str] | None = None) -> None:
        self.resume_calls = 0
        self._events = events if events is not None else []

    async def resume_running_jobs(self) -> None:
        self._events.append("resume")
        self.resume_calls += 1


class _EmbeddingProvider:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.wait_calls = 0

    async def wait_until_ready(self) -> None:
        self._events.append("embedding_ready")
        self.wait_calls += 1


def test_lifespan_waits_for_embedding_provider_before_resuming_jobs(monkeypatch) -> None:
    events: list[str] = []
    coordinator = _Coordinator(events)
    embedding_provider = _EmbeddingProvider(events)

    async def _fake_build_app_container_async(**kwargs):
        del kwargs
        return {
            "embedding_provider": embedding_provider,
            "background_job_coordinator": coordinator,
        }

    monkeypatch.setattr(
        smart_service_sdk,
        "build_app_container_async",
        _fake_build_app_container_async,
    )
    monkeypatch.setattr(
        smart_service_sdk.settings,
        "ENABLE_RESUME_RUNNING_BACKGROUND_JOBS",
        True,
    )

    async def scenario() -> None:
        app = FastAPI()
        async with smart_service_sdk.lifespan(app):
            assert embedding_provider.wait_calls == 1
            assert coordinator.resume_calls == 1

    asyncio.run(scenario())

    assert events == ["embedding_ready", "resume"]


def test_lifespan_skips_resume_when_resume_setting_disabled(monkeypatch) -> None:
    events: list[str] = []
    coordinator = _Coordinator(events)
    embedding_provider = _EmbeddingProvider(events)

    async def _fake_build_app_container_async(**kwargs):
        del kwargs
        return {
            "embedding_provider": embedding_provider,
            "background_job_coordinator": coordinator,
        }

    monkeypatch.setattr(
        smart_service_sdk,
        "build_app_container_async",
        _fake_build_app_container_async,
    )
    monkeypatch.setattr(
        smart_service_sdk.settings,
        "ENABLE_RESUME_RUNNING_BACKGROUND_JOBS",
        False,
    )

    async def scenario() -> None:
        app = FastAPI()
        async with smart_service_sdk.lifespan(app):
            assert embedding_provider.wait_calls == 1
            assert coordinator.resume_calls == 0

    asyncio.run(scenario())

    assert events == ["embedding_ready"]
