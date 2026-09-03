from __future__ import annotations

import asyncio
import threading


def test_runtime_runs_two_handlers_concurrently_with_two_slots():
    from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
        ConsumerDispatchRuntime,
    )

    runtime = ConsumerDispatchRuntime(
        max_in_flight=2, drain_timeout_seconds=1, logger=None
    )
    started_one = threading.Event()
    started_two = threading.Event()
    release = threading.Event()

    async def _handler(name: str) -> None:
        if name == "one":
            started_one.set()
        else:
            started_two.set()
        await asyncio.to_thread(release.wait)

    runtime.submit(_handler("one"))
    runtime.submit(_handler("two"))

    assert started_one.wait(timeout=1)
    assert started_two.wait(timeout=1)

    release.set()
    runtime.drain_and_close()


def test_runtime_third_submit_waits_until_slot_is_released():
    from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
        ConsumerDispatchRuntime,
    )

    runtime = ConsumerDispatchRuntime(
        max_in_flight=2, drain_timeout_seconds=1, logger=None
    )
    started_one = threading.Event()
    started_two = threading.Event()
    hold = threading.Event()
    third_submitted = threading.Event()

    async def _handler(name: str) -> None:
        if name == "one":
            started_one.set()
        elif name == "two":
            started_two.set()
        await asyncio.to_thread(hold.wait)

    runtime.submit(_handler("one"))
    runtime.submit(_handler("two"))
    assert started_one.wait(timeout=1)
    assert started_two.wait(timeout=1)

    def _submit_third() -> None:
        runtime.submit(_handler("three"))
        third_submitted.set()

    thread = threading.Thread(target=_submit_third)
    thread.start()

    assert third_submitted.wait(timeout=0.1) is False
    hold.set()
    thread.join(timeout=1)
    assert third_submitted.is_set()

    runtime.drain_and_close()


def test_runtime_submit_raises_after_close_for_new_work():
    from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
        ConsumerDispatchRuntime,
    )

    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    runtime.close_for_new_work()

    async def _handler() -> None:
        return None

    try:
        runtime.submit(_handler())
    except RuntimeError as exc:
        assert str(exc) == "dispatch runtime is closing"
    else:
        raise AssertionError("submit() must raise once the runtime is closing")
    finally:
        runtime.drain_and_close()
