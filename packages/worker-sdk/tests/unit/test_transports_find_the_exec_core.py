"""Every transport must find the exec core in a REAL bootstrap container.

``build_app_container`` names each discovered feature ``<module>_usecase``
(``bootstrap.py``: ``registry[f"{module_name}_usecase"]``), so a container built
the documented way carries ``execute_task_usecase`` and never ``execute_task``.
``worker_pull`` and the gRPC servicer read the SHORT name — so PULL raised
``"PULL mode requires an execute_task use case in the container"`` on startup for
every worker built with ``run_worker(...)``, and gRPC aborted UNIMPLEMENTED.

It survived because **every** pull and gRPC test hand-builds ``{"execute_task": …}``
(``test_pull_worker.py``, ``test_pull_heartbeat.py``, ``test_grpc_execution_servicer.py``)
— a container shape the bootstrap does not produce. Mocking the wiring is exactly
what hid the wiring bug.

So these tests deliberately use the real ``build_app_container()``. That is the
whole point of the file: nothing here may substitute the container.
"""
from __future__ import annotations

import asyncio

import pytest

from worker_sdk.bootstrap import build_app_container
from worker_sdk.layer3_adapters.controllers import worker_pull


def test_bootstrap_names_the_feature_with_the_usecase_suffix():
    """Pins the naming convention the tolerant lookups exist to bridge.

    If this ever starts producing a bare ``execute_task``, the ``or`` fallbacks in
    ``worker_pull`` / the gRPC servicer can be simplified — and this test is where
    you find that out.
    """
    container = build_app_container()
    assert container.get("execute_task_usecase") is not None
    assert container.get("execute_task") is None


def test_the_short_key_alone_is_not_enough_to_start_pull():
    """States the bug in one line so a future refactor cannot quietly restore it."""
    container = build_app_container()
    assert (
        container.get("execute_task") or container.get("execute_task_usecase")
    ) is not None, "no transport can find the exec core in a real container"


class _StubPullClient:
    """Ends the lease loop immediately; we are testing startup, not the loop."""

    def __init__(self, *args, **kwargs) -> None:
        self.closed = False

    async def lease(self, *args, **kwargs):
        raise asyncio.CancelledError

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_pull_starts_against_a_real_bootstrap_container(monkeypatch):
    """The regression test proper: this raised RuntimeError before the fix.

    ``run_pull_worker`` resolves the exec core BEFORE it builds its HTTP client,
    so reaching the (stubbed) lease call at all proves the lookup succeeded.
    """
    container = build_app_container()
    # No registry: registration is a network call and is not what this pins.
    container["_dependencies"]["worker_registry"] = None
    monkeypatch.setattr(worker_pull, "PullTaskClient", _StubPullClient)

    await worker_pull.run_pull_worker(container)   # must not raise


@pytest.mark.asyncio
async def test_pull_still_refuses_a_container_with_no_exec_core(monkeypatch):
    """The tolerant lookup must not become a silent no-op — a container with
    neither key is a real misconfiguration and has to fail loudly."""
    monkeypatch.setattr(worker_pull, "PullTaskClient", _StubPullClient)
    with pytest.raises(RuntimeError, match="execute_task"):
        await worker_pull.run_pull_worker({"_dependencies": {}})
