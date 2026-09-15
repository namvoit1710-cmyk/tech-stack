"""Regression: ``TaskNodeHandler._resolve_function_calls`` must not block the loop.

The worker-registry client's ``invoke_worker_function`` is SYNCHRONOUS (it uses a
blocking ``httpx.Client``). If the handler calls it inline, the control-plane
event loop is blocked for the whole executor->worker round-trip. When the invoked
function is the gateway-worker's ``call_internal``, the worker must call BACK into
the control-plane (``POST /api/v1/gateway/services/{svc}/_resolve``); a blocked
loop cannot serve that reentrant request, so the resolve fails
(``REGISTRY_UNAVAILABLE``) and the gateway call returns an error. The fix
dispatches the sync call via ``asyncio.to_thread`` so the loop stays free.

This proves the loop stays free: while the sync call blocks in a worker thread, a
concurrent coroutine runs and releases it. Under the old inline call the releaser
never gets scheduled and the blocking call times out, so the assertion fails.
"""
import asyncio
import threading
from types import SimpleNamespace

from app.layer2_application.node_handlers.task_handler import TaskNodeHandler


async def test_resolve_function_calls_does_not_block_event_loop():
    started = threading.Event()
    released = threading.Event()

    class BlockingWorkerClient:
        def invoke_worker_function(self, worker_type, function_name, params):
            started.set()
            # Block until a concurrent coroutine releases us. If the event loop
            # is blocked (inline sync call), the releaser never runs -> timeout.
            if not released.wait(timeout=3.0):
                return {"status": "error", "error": "event loop was blocked"}
            return {"status": "success", "result": {"ok": True}}

    handler = TaskNodeHandler(
        variable_resolver=None, worker_registry_client=BlockingWorkerClient()
    )
    node = SimpleNamespace(worker_type="gateway-worker")
    # Empty params keeps the resolver out of the picture; the marker still fires.
    resolved_input = {"result": {"_function_call": {"worker_function": "f", "params": {}}}}

    async def releaser():
        # Spin on the loop until the sync call has started in its thread; if the
        # loop were blocked, this coroutine would never get a chance to run.
        while not started.is_set():
            await asyncio.sleep(0.01)
        released.set()

    result, _ = await asyncio.gather(
        handler._resolve_function_calls(node, resolved_input, None),
        releaser(),
    )

    # The function result was spliced in — proving the call completed because the
    # loop served the (simulated) reentrant work, not because it blocked.
    assert result["result"] == {"ok": True}
