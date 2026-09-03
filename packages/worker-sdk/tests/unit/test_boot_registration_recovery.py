"""R8 (SA-2055) §4 / B-B — a worker that boots against a dead executor.

`asyncio.create_task(_heartbeat_loop())` sits INSIDE the try around register()
(worker_server:159-161). register() throws -> the heartbeat task is never
created -> the worker is silently unregistered FOREVER. "continuing anyway" is
a lie: it continues as a zombie.

Note the spec's claim that "the loop's re_register path IS the retry" does not
hold on its own: re_register only arrives on a heartbeat RESPONSE, and you can
only heartbeat a wid you already have. With the boot register failed there is no
wid, so a merely-hoisted loop retries nothing. Hence: a failed register parks a
BLANK wid, and the loop treats blank-wid as "register me".
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition


def _server_settings(**over):
    base = dict(
        APP_NAME="Test", WORKER_TYPE="test", WORKER_VERSION="1.0", SDK_VERSION="1.0.0",
        PROXY_URL="http://x", WORKER_NAME="", WORKER_DESCRIPTION="",
        WORKER_NODE_CLASS="TECHNICAL", WORKER_ICON="Cog", WORKER_COLOR="#3B82F6",
        SERVER_HOST="0.0.0.0", SERVER_PORT=35000,
        HEARTBEAT_INTERVAL_SECONDS=0.01, MAX_WORKER_THREADS=2,
    )
    base.update(over)
    return MagicMock(**base)


def _fake_app(deps):
    """_lifespan only ever touches app.state.dependencies — a real FastAPI app is
    unnecessary and would drag uvicorn/TestClient into an async test."""
    return SimpleNamespace(state=SimpleNamespace(dependencies=deps))


def _registry(**over):
    r = AsyncMock()
    r.register = over.get("register", AsyncMock(return_value="w-1"))
    r.heartbeat = over.get("heartbeat", AsyncMock(return_value={}))
    r.deregister = AsyncMock()
    r.close = AsyncMock()
    return r


class TestServerPerTypeIsolation:
    @pytest.mark.asyncio
    async def test_one_types_failure_does_not_skip_the_rest(self, monkeypatch):
        """jira-worker registers 22 types in one loop inside one try. Today, if
        type 1's register throws, types 2..22 are never even attempted."""
        from worker_sdk.layer3_adapters.controllers import worker_server as ws
        # 30s interval: the heartbeat loop never gets a turn, so call_count
        # measures the BOOT path only, with no retries mixed in.
        monkeypatch.setattr(ws, "settings", _server_settings(HEARTBEAT_INTERVAL_SECONDS=30))

        registry = _registry(register=AsyncMock(side_effect=[RuntimeError("boom"), "w-2"]))
        deps = {
            "worker_registry": registry,
            "node_type_registry": {
                "t1": NodeTypeDefinition(worker_type="t1"),
                "t2": NodeTypeDefinition(worker_type="t2"),
            },
        }
        async with ws._lifespan(_fake_app(deps)):
            await asyncio.sleep(0)

        assert registry.register.call_count == 2, "type 2 was never attempted (B-B)"

    @pytest.mark.asyncio
    async def test_a_blank_wid_is_never_deregistered(self, monkeypatch):
        """A failed register parks ("", reg). Shutdown must not deregister ""."""
        from worker_sdk.layer3_adapters.controllers import worker_server as ws
        monkeypatch.setattr(ws, "settings", _server_settings(HEARTBEAT_INTERVAL_SECONDS=30))

        registry = _registry(register=AsyncMock(side_effect=RuntimeError("executor down")))
        async with ws._lifespan(_fake_app({"worker_registry": registry})):
            await asyncio.sleep(0)

        registry.deregister.assert_not_called()


class TestServerBootRecovery:
    @pytest.mark.asyncio
    async def test_register_fails_at_boot_then_recovers(self, monkeypatch):
        """§9 checkbox 9. The executor is down when the worker boots. Today:
        create_task never runs, no heartbeat task exists, the worker is a zombie
        forever. After: the loop starts anyway and re-registers within one
        interval."""
        from worker_sdk.layer3_adapters.controllers import worker_server as ws
        monkeypatch.setattr(ws, "settings", _server_settings())

        # Down for the first attempt, up thereafter.
        registry = _registry(register=AsyncMock(side_effect=[RuntimeError("executor down"), "w-late"]))
        async with ws._lifespan(_fake_app({"worker_registry": registry})):
            await asyncio.sleep(0.05)    # ~5 heartbeat intervals at 0.01s

        assert registry.register.call_count >= 2, "the boot failure was never retried (B-B)"
        # It recovered, so shutdown deregisters the id it eventually obtained.
        registry.deregister.assert_called_once_with("w-late")

    @pytest.mark.asyncio
    async def test_heartbeat_loop_starts_even_when_register_throws(self, monkeypatch):
        """The narrow B-B claim: create_task must not be skipped."""
        from worker_sdk.layer3_adapters.controllers import worker_server as ws
        monkeypatch.setattr(ws, "settings", _server_settings())

        registry = _registry(register=AsyncMock(side_effect=RuntimeError("executor down")))
        async with ws._lifespan(_fake_app({"worker_registry": registry})):
            await asyncio.sleep(0.05)

        # Never succeeds, but keeps trying — a live retry loop, not a zombie.
        assert registry.register.call_count >= 3
        registry.deregister.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovered_worker_then_heartbeats_normally(self, monkeypatch):
        from worker_sdk.layer3_adapters.controllers import worker_server as ws
        monkeypatch.setattr(ws, "settings", _server_settings())

        registry = _registry(register=AsyncMock(side_effect=[RuntimeError("down"), "w-late"]))
        async with ws._lifespan(_fake_app({"worker_registry": registry})):
            await asyncio.sleep(0.08)

        assert registry.heartbeat.call_count >= 1, "recovered but never heartbeated"
        assert registry.heartbeat.await_args.args[0] == "w-late"


from worker_sdk.layer3_adapters.controllers.worker_headless import run_headless_worker


def _headless_settings(**over):
    base = dict(WORKER_TYPE="test", WORKER_VERSION="1.0", SDK_VERSION="1.0.0",
                WORKER_NAME="", WORKER_DESCRIPTION="", WORKER_NODE_CLASS="TECHNICAL",
                WORKER_ICON="Cog", WORKER_COLOR="#3B82F6",
                HEARTBEAT_INTERVAL_SECONDS=0.01, MAX_WORKER_THREADS=2)
    base.update(over)
    return MagicMock(**base)


async def _run_headless_briefly(container, seconds=0.05):
    task = asyncio.create_task(run_headless_worker(container))
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestHeadlessPerTypeIsolation:
    @pytest.mark.asyncio
    async def test_one_types_failure_does_not_skip_the_rest(self, monkeypatch):
        monkeypatch.setattr(
            "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
            _headless_settings(HEARTBEAT_INTERVAL_SECONDS=30),
        )
        registry = _registry(register=AsyncMock(side_effect=[RuntimeError("boom"), "w-2"]))
        await _run_headless_briefly({
            "_dependencies": {
                "worker_registry": registry,
                "logger": MagicMock(),
                "node_type_registry": {
                    "t1": NodeTypeDefinition(worker_type="t1"),
                    "t2": NodeTypeDefinition(worker_type="t2"),
                },
            }
        })
        # NOTE (deviation from brief, see r8-task-1112-report.md): unlike
        # worker_server's _heartbeat_loop (sleeps THEN processes), the
        # HEADLESS main loop processes registrations THEN sleeps, so its very
        # first tick retries t1's blank wid immediately regardless of the 30s
        # interval above -- Task 12's retry adds a 3rd register() call here.
        # ">=2" still proves the original claim (type 2 was attempted).
        assert registry.register.call_count >= 2, "type 2 was never attempted (B-B)"

    @pytest.mark.asyncio
    async def test_a_blank_wid_is_never_deregistered(self, monkeypatch):
        monkeypatch.setattr(
            "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
            _headless_settings(HEARTBEAT_INTERVAL_SECONDS=30),
        )
        registry = _registry(register=AsyncMock(side_effect=RuntimeError("executor down")))
        await _run_headless_briefly({"_dependencies": {"worker_registry": registry, "logger": MagicMock()}})
        registry.deregister.assert_not_called()


class TestHeadlessBootRecovery:
    @pytest.mark.asyncio
    async def test_register_fails_at_boot_then_recovers(self, monkeypatch):
        """§9 checkbox 9, HEADLESS. Unlike SERVER there is no create_task to
        hoist — the main loop already runs outside the register try. What was
        missing is anything for it to retry."""
        monkeypatch.setattr(
            "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
            _headless_settings(),
        )
        registry = _registry(register=AsyncMock(side_effect=[RuntimeError("executor down"), "w-late"]))
        await _run_headless_briefly({"_dependencies": {"worker_registry": registry, "logger": MagicMock()}})

        assert registry.register.call_count >= 2, "the boot failure was never retried (B-B)"
        registry.deregister.assert_called_once_with("w-late")

    @pytest.mark.asyncio
    async def test_keeps_retrying_while_the_executor_stays_down(self, monkeypatch):
        monkeypatch.setattr(
            "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
            _headless_settings(),
        )
        registry = _registry(register=AsyncMock(side_effect=RuntimeError("executor down")))
        await _run_headless_briefly({"_dependencies": {"worker_registry": registry, "logger": MagicMock()}})
        assert registry.register.call_count >= 3
        registry.deregister.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_type_boot_failure_recovers_all_types(self, monkeypatch):
        """Test-quality-bar item: a boot-fail HEADLESS worker with 2+ node
        types recovers ALL of them, not just one. Both t1 and t2 fail their
        boot register; the main loop's retry (Task 12) must pick up both."""
        monkeypatch.setattr(
            "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
            _headless_settings(),
        )
        registry = _registry(register=AsyncMock(side_effect=[
            RuntimeError("executor down"), RuntimeError("executor down"), "w-1", "w-2",
        ]))
        await _run_headless_briefly({
            "_dependencies": {
                "worker_registry": registry,
                "logger": MagicMock(),
                "node_type_registry": {
                    "t1": NodeTypeDefinition(worker_type="t1"),
                    "t2": NodeTypeDefinition(worker_type="t2"),
                },
            }
        })
        assert registry.register.call_count >= 4, "not both types were retried (B-B)"
        deregistered_ids = {c.args[0] for c in registry.deregister.await_args_list}
        assert deregistered_ids == {"w-1", "w-2"}, "both recovered types must be deregistered on shutdown"
