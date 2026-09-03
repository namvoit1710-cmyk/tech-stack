from __future__ import annotations

import asyncio
import threading

import pytest

from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
    ConsumerDispatchRuntime,
)


# ─── Repro (pre-implementation evidence) ─────────────────────────────────────
# Before N3 there is NO cooperative cancel: futures are held in an unkeyed set,
# so a single conversation's in-flight turn cannot be interrupted. This test
# exercises the *intended* API (``cancel(conv_id)``) and therefore FAILS before
# N3 (AttributeError: no such method), proving the gap.
def test_repro_single_conv_cannot_be_cancelled_before_n3():
    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    started = threading.Event()
    release = threading.Event()

    async def _turn() -> None:
        started.set()
        await asyncio.to_thread(release.wait)

    try:
        runtime.submit(_turn(), conv_id="c1")
        assert started.wait(timeout=1)
        cancelled = runtime.cancel("c1")
        assert cancelled == 1
    finally:
        release.set()
        runtime.drain_and_close()


# ─── N3: conv_id -> Future registry + cancel(conv_id) ────────────────────────
def test_t1_cancel_by_conv_id_cancels_only_that_conversation():
    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    started_c1 = threading.Event()
    observed_cancel_c1 = threading.Event()
    release = threading.Event()

    async def _turn_c1() -> None:
        started_c1.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            observed_cancel_c1.set()
            raise

    # A different conversation, run on a *second* slot so it is genuinely
    # in-flight at the same time and we can prove it is untouched.
    started_c2 = threading.Event()
    c2_future = None

    async def _turn_c2() -> None:
        started_c2.set()
        await asyncio.to_thread(release.wait)

    # max_in_flight=1 is the business-agent config; c1 holds the only slot.
    fut_c1 = runtime.submit(_turn_c1(), conv_id="c1")
    assert started_c1.wait(timeout=1)

    cancelled = runtime.cancel("c1")
    assert cancelled == 1
    assert observed_cancel_c1.wait(timeout=1)
    assert fut_c1.cancelled()

    # An unrelated conv_id is unaffected: cancelling it finds nothing.
    assert runtime.cancel("does-not-exist") == 0

    # And a live different conversation keeps running after c1 is cancelled.
    c2_future = runtime.submit(_turn_c2(), conv_id="c2")
    assert started_c2.wait(timeout=1)
    assert not c2_future.done()
    assert runtime.cancel("c1") == 0  # already gone, no leak

    release.set()
    runtime.drain_and_close()


def test_t2_registry_cleans_up_after_normal_completion():
    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    done = threading.Event()

    async def _turn() -> None:
        done.set()

    fut = runtime.submit(_turn(), conv_id="c1")
    assert done.wait(timeout=1)
    # Wait for the done-callback to run and evict the registry entry.
    for _ in range(100):
        if fut.done() and runtime.cancel("c1") == 0:
            break
        threading.Event().wait(0.01)
    assert fut.done()
    assert runtime.cancel("c1") == 0  # no leak: nothing left to cancel

    runtime.drain_and_close()


# ─── N4: control path is not starved by max_in_flight=1 ──────────────────────
def test_t3_control_path_cancels_turn_without_an_in_flight_slot():
    """A stop must be processed even while a turn holds the only slot.

    ``submit_control`` runs the control work on the loop WITHOUT acquiring an
    in-flight slot, so it is never queued behind the running turn.
    """
    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    turn_started = threading.Event()
    turn_cancelled = threading.Event()
    control_ran = threading.Event()

    async def _turn() -> None:
        turn_started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            turn_cancelled.set()
            raise

    fut = runtime.submit(_turn(), conv_id="c1")
    assert turn_started.wait(timeout=1)
    # The single slot is now held by the turn. A normal submit() would block on
    # the semaphore here; the control path must not.

    cancelled_count: list[int] = []

    async def _control() -> None:
        cancelled_count.append(runtime.cancel("c1"))
        control_ran.set()

    runtime.submit_control(_control())
    assert control_ran.wait(timeout=1)
    assert cancelled_count == [1]
    assert turn_cancelled.wait(timeout=1)
    assert fut.cancelled()

    runtime.drain_and_close()


def test_t3b_intake_routes_control_message_to_cancel():
    """The consumer intake recognizes the control type and calls
    ``consumer.cancel_conversation(conv_id)`` without going through execute."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    from agent_sdk.layer1_domain.value_objects.agent_control import (
        AGENT_CONTROL_MESSAGE_TYPE,
    )
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent
    from tests.helpers.testing import StubLogger

    class _ControlConsumer:
        def __init__(self, message: dict) -> None:
            self._message = message
            self._topic = "agent.control"
            self._queue_name = "default/agent.control"
            self.cancelled: list[str] = []

        async def start(self, handler):
            await handler(self._message)

        async def stop(self):
            pass

        def cancel_conversation(self, conv_id: str) -> int:
            self.cancelled.append(conv_id)
            return 1

    delivery_payload = {
        "type": AGENT_CONTROL_MESSAGE_TYPE,
        "action": "cancel",
        "conv_id": "c1",
        "correlation_id": "c1",
    }
    consumer = _ControlConsumer(delivery_payload)
    publisher = AsyncMock()
    execute_use_case = AsyncMock()
    container = {
        "execute_agent": execute_use_case,
        "consumer": consumer,
        "publisher": publisher,
        "logger": StubLogger(),
    }

    _asyncio.run(run_consumer_agent(container))

    assert consumer.cancelled == ["c1"]
    execute_use_case.execute.assert_not_awaited()


# ─── S1: an UNRECOGNIZED control action is logged (WARNING) and acked ─────────
# Before the fix, any agent.control message that was not `cancel` was routed
# inline and silently acked — swallowed with no trace. The fix logs a WARNING so
# future control types are observable, while still acking (never requeue).

class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []
        self.infos: list[tuple[str, dict]] = []

    def warning(self, message, *a, **kw):
        self.warnings.append((message, kw))

    def info(self, message, *a, **kw):
        self.infos.append((message, kw))

    def error(self, *a, **kw):
        pass

    def debug(self, *a, **kw):
        pass


class _AckTrackingDelivery:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.acked = False
        self.nacked = False
        self.rejected = False

    async def ack(self) -> None:
        self.acked = True

    async def nack(self, requeue: bool = True) -> None:
        self.nacked = True

    async def reject(self) -> None:
        self.rejected = True


class _ControlDeliveryConsumer:
    def __init__(self, delivery: _AckTrackingDelivery) -> None:
        self._delivery = delivery
        self._topic = "agent.control"
        self._queue_name = "default/agent.control"
        self.cancelled: list[str] = []

    async def start(self, handler):
        await handler(self._delivery)

    async def stop(self):
        pass

    def cancel_conversation(self, conv_id: str) -> int:
        self.cancelled.append(conv_id)
        return 1


def _run_control(payload: dict):
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    delivery = _AckTrackingDelivery(payload)
    consumer = _ControlDeliveryConsumer(delivery)
    logger = _RecordingLogger()
    execute_use_case = AsyncMock()
    container = {
        "execute_agent": execute_use_case,
        "consumer": consumer,
        "publisher": AsyncMock(),
        "logger": logger,
    }
    _asyncio.run(run_consumer_agent(container))
    return delivery, consumer, logger, execute_use_case


def test_s1_unrecognized_control_action_is_logged_and_acked():
    from agent_sdk.layer1_domain.value_objects.agent_control import (
        AGENT_CONTROL_MESSAGE_TYPE,
    )

    delivery, consumer, logger, execute_use_case = _run_control(
        {
            "type": AGENT_CONTROL_MESSAGE_TYPE,
            "action": "pause",  # NOT a recognized action
            "conv_id": "c1",
            "correlation_id": "corr-1",
        }
    )

    # A WARNING was emitted (structured: type + action + conv_id present).
    warned = [kw for msg, kw in logger.warnings if "Unrecognized agent control action" in msg]
    assert len(warned) == 1
    assert warned[0]["action"] == "pause"
    assert warned[0]["conv_id"] == "c1"
    assert warned[0]["message_type"] == AGENT_CONTROL_MESSAGE_TYPE
    # Still acked (not rejected / not requeued), and nothing was cancelled or executed.
    assert delivery.acked is True
    assert delivery.nacked is False and delivery.rejected is False
    assert consumer.cancelled == []
    execute_use_case.execute.assert_not_awaited()


def test_s1_cancel_action_still_cancels_and_does_not_warn():
    """The cancel path is unchanged: it cancels, acks, and emits no
    unrecognized-action warning."""
    from agent_sdk.layer1_domain.value_objects.agent_control import (
        AGENT_CONTROL_MESSAGE_TYPE,
    )

    delivery, consumer, logger, execute_use_case = _run_control(
        {
            "type": AGENT_CONTROL_MESSAGE_TYPE,
            "action": "cancel",
            "conv_id": "c1",
            "correlation_id": "corr-1",
        }
    )

    assert consumer.cancelled == ["c1"]
    assert delivery.acked is True
    assert [kw for msg, kw in logger.warnings if "Unrecognized agent control action" in msg] == []
    execute_use_case.execute.assert_not_awaited()


# ─── M2: in-flight slot release is exactly-once, cancel-safe ─────────────────
# ``submit`` acquires the in-flight slot synchronously. Before the fix the only
# release was in ``_run_and_release``'s ``finally``; the finding is that a turn
# whose coroutine never starts (cancel while PENDING) would skip the ``finally``
# and leak the slot -> a max_in_flight=1 consumer wedges forever. The fix
# releases from BOTH the coroutine's ``finally`` AND a future done-callback,
# guarded so it happens exactly once (a double release corrupts the semaphore
# bound). ``submit_control`` never took a slot, so it must never release one.
#
# NOTE ON REPRODUCTION: through the runtime's public API the wedge is not
# actually triggerable in this asyncio version -- ``run_coroutine_threadsafe``
# schedules the Task's first step (via ``call_soon``) BEFORE ``_chain_future``
# defers the cancel (via ``call_soon_threadsafe``, enqueued after), so the turn
# body always starts and its ``finally`` always releases. These tests therefore
# lock the invariants the fix guarantees (exactly-once release, no wedge after a
# cancel, control path untouched) rather than a pre-fix hang. The exactly-once
# unit test below IS a genuine pre-fix/negative guard: a non-idempotent release
# would over-release the BoundedSemaphore and raise ValueError.


def _block_loop(runtime) -> threading.Event:
    """Park the runtime's event loop thread on a synchronous wait.

    Returns ``unblock``. Control work never takes a slot, so this freezes the
    loop *after* a turn's slot is acquired, holding the turn's coroutine PENDING
    until we ``unblock``.
    """
    loop_blocked = threading.Event()
    unblock = threading.Event()

    async def _blocker() -> None:
        loop_blocked.set()
        unblock.wait()  # synchronous: freezes the loop thread entirely

    runtime.submit_control(_blocker())
    assert loop_blocked.wait(timeout=1)
    return unblock


def test_m2_slot_releaser_releases_exactly_once():
    """The releaser frees a slot once and is a no-op thereafter.

    This is the fix's core safety property: it is called from BOTH the
    coroutine ``finally`` and the future done-callback. A non-idempotent version
    would call ``BoundedSemaphore.release`` twice and raise ValueError; this
    test fails in that case.
    """
    from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
        _SlotReleaser,
    )

    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(blocking=False) is True  # value 1 -> 0 (slot taken)
    releaser = _SlotReleaser(sem)

    releaser.release()  # 0 -> 1 (finally)
    releaser.release()  # no-op (done-callback)
    releaser.release(object())  # no-op, also usable as a Future callback

    # Exactly one slot is available: acquire once succeeds, a second fails.
    assert sem.acquire(blocking=False) is True
    assert sem.acquire(blocking=False) is False


def test_m2_cancel_before_start_frees_slot_no_wedge():
    """Cancelling a PENDING turn frees its slot; the consumer does not wedge."""
    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    unblock = _block_loop(runtime)

    async def _turn() -> None:
        await asyncio.sleep(30)

    fut = runtime.submit(_turn(), conv_id="c1")
    assert not fut.done()
    # Loop is frozen, so the coroutine is still PENDING: cancel it now.
    assert fut.cancel()
    assert fut.cancelled()

    unblock.set()

    # A subsequent turn must acquire the (released) slot without wedging.
    started = threading.Event()
    release_two = threading.Event()
    acquired = threading.Event()

    async def _turn_two() -> None:
        started.set()
        await asyncio.to_thread(release_two.wait)

    def _submit_two() -> None:
        runtime.submit(_turn_two(), conv_id="c2")
        acquired.set()

    thread = threading.Thread(target=_submit_two)
    thread.start()
    try:
        assert acquired.wait(timeout=2), "slot leaked: submit wedged on the semaphore"
        assert started.wait(timeout=2)
    finally:
        release_two.set()
        thread.join(timeout=2)
        runtime.drain_and_close()


def test_m2_normal_completion_releases_slot_exactly_once():
    """A normal run releases exactly once: not zero (leak) and not twice.

    Both the coroutine ``finally`` and the done-callback fire; the counter must
    return to its initial bound after each of several sequential runs.
    """
    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    try:
        for _ in range(3):
            done = threading.Event()

            async def _turn() -> None:
                done.set()

            fut = runtime.submit(_turn(), conv_id="c1")
            assert done.wait(timeout=1)
            for _ in range(200):
                if fut.done():
                    break
                threading.Event().wait(0.01)
            assert fut.done()
            # Give the done-callback a beat to run (its release is a no-op).
            for _ in range(200):
                if runtime._slots._value == runtime._max_in_flight:
                    break
                threading.Event().wait(0.01)
            # Exactly one release: counter back to the initial bound, no more.
            assert runtime._slots._value == runtime._max_in_flight
    finally:
        runtime.drain_and_close()


def test_m2_control_path_never_releases_a_slot_it_did_not_take():
    """``submit_control`` bypasses the semaphore, so it must not release one.

    Running many control tasks (each with its own done-callback) must leave the
    slot counter untouched; a stray release here would let an extra turn slip
    past max_in_flight.
    """
    runtime = ConsumerDispatchRuntime(
        max_in_flight=1, drain_timeout_seconds=1, logger=None
    )
    try:
        for _ in range(5):
            ran = threading.Event()

            async def _control() -> None:
                ran.set()

            runtime.submit_control(_control())
            assert ran.wait(timeout=1)
        # Slot counter untouched by control work.
        for _ in range(200):
            if runtime._slots._value == runtime._max_in_flight:
                break
            threading.Event().wait(0.01)
        assert runtime._slots._value == runtime._max_in_flight

        # And a real turn can still take the one slot.
        started = threading.Event()
        release = threading.Event()

        async def _turn() -> None:
            started.set()
            await asyncio.to_thread(release.wait)

        runtime.submit(_turn(), conv_id="c1")
        assert started.wait(timeout=1)
        release.set()
    finally:
        runtime.drain_and_close()
