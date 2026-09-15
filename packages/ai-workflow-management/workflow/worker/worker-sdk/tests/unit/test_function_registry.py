"""Unit specs for the layer2 in-process FunctionRegistry service.

Target: worker_sdk/layer2_application/services/function_registry.py (was 81%,
the get/list_names branches + both invoke failure paths were unexercised).

Cases per branch — happy, edge, invalid input, boundary, failure path — grounded
in the source: register() overwrites by name; get() returns None for a miss;
invoke() awaits the handler, raises KeyError on an unknown name and ValueError
when the registered function has handler=None. The handler is an async callable
(``await func.handler(params)``) so it is mocked with AsyncMock, per the domain
invariant "async collaborator -> AsyncMock".
"""

from unittest.mock import AsyncMock

import pytest

from worker_sdk.layer2_application.services.function_registry import FunctionRegistry
from worker_sdk.layer1_domain.entities.worker_function import (
    WorkerFunction,
    WorkerFunctionDefinition,
)


def _fn(name="echo", handler=None, description="d"):
    return WorkerFunction(name=name, description=description, handler=handler)


# --- register / get ---------------------------------------------------------

def test_register_then_get_returns_same_instance():
    reg = FunctionRegistry()
    fn = _fn("echo")
    reg.register(fn)
    assert reg.get("echo") is fn


def test_get_unknown_returns_none():
    # source: get() -> self._functions.get(name) -> None on miss (line 20).
    reg = FunctionRegistry()
    assert reg.get("nope") is None


def test_register_same_name_overwrites():
    # source: register() keys by func.name, so a second register replaces.
    reg = FunctionRegistry()
    first = _fn("dup", description="first")
    second = _fn("dup", description="second")
    reg.register(first)
    reg.register(second)
    assert reg.get("dup") is second
    assert reg.list_names() == ["dup"]  # not duplicated


# --- list_names / list_definitions (boundary: empty) ------------------------

def test_list_names_empty_registry():
    # boundary: a fresh registry has no functions (line 22/23).
    reg = FunctionRegistry()
    assert reg.list_names() == []


def test_list_names_after_registering():
    reg = FunctionRegistry()
    reg.register(_fn("a"))
    reg.register(_fn("b"))
    assert reg.list_names() == ["a", "b"]


def test_list_definitions_are_metadata_only():
    # to_definition() drops the handler; definitions carry name/description/schemas.
    reg = FunctionRegistry()
    reg.register(WorkerFunction(name="f", description="desc", handler=AsyncMock()))
    defs = reg.list_definitions()
    assert len(defs) == 1
    assert isinstance(defs[0], WorkerFunctionDefinition)
    assert defs[0].name == "f"
    assert defs[0].description == "desc"
    assert not hasattr(defs[0], "handler")


def test_list_definitions_empty_registry():
    # boundary: no functions -> empty list.
    assert FunctionRegistry().list_definitions() == []


# --- invoke (async) ---------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_happy_awaits_handler_with_params():
    # happy: invoke awaits the async handler with the params dict and returns it.
    handler = AsyncMock(return_value={"ok": True})
    reg = FunctionRegistry()
    reg.register(_fn("run", handler=handler))
    params = {"x": 1}
    result = await reg.invoke("run", params)
    assert result == {"ok": True}
    handler.assert_awaited_once_with(params)


@pytest.mark.asyncio
async def test_invoke_unknown_name_raises_keyerror():
    # invalid input / failure path: unknown name -> KeyError (line 31).
    reg = FunctionRegistry()
    with pytest.raises(KeyError, match="Unknown function: ghost"):
        await reg.invoke("ghost", {})


@pytest.mark.asyncio
async def test_invoke_handler_none_raises_valueerror():
    # failure path: a registered function without a handler -> ValueError (line 33).
    reg = FunctionRegistry()
    reg.register(_fn("noop", handler=None))
    with pytest.raises(ValueError, match="Function 'noop' has no handler"):
        await reg.invoke("noop", {})


@pytest.mark.asyncio
async def test_invoke_propagates_handler_error():
    # failure path: an exception raised inside the handler propagates unchanged.
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    reg = FunctionRegistry()
    reg.register(_fn("bad", handler=handler))
    with pytest.raises(RuntimeError, match="boom"):
        await reg.invoke("bad", {})
