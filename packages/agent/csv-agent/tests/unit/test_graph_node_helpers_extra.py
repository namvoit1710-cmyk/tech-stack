import asyncio

from app.layer2_application.graphs.nodes import execute_node as exec_module
from app.layer2_application.graphs.nodes import plan_node as plan_module


class _BrokenModelSchema:
    @staticmethod
    def model_json_schema():
        raise RuntimeError("broken")


class _BrokenLegacySchema:
    @staticmethod
    def schema():
        raise RuntimeError("broken")


class _LegacySchema:
    @staticmethod
    def schema():
        return {"properties": {"file_id": {"type": "string"}}}


def test_execute_node_schema_helpers_tolerate_broken_schemas_and_values():
    assert exec_module._schema_dict(_BrokenModelSchema()) == {}
    assert exec_module._schema_dict(_BrokenLegacySchema()) == {}
    assert exec_module._coerce_tool_result("plain text") == {"value": "plain text"}
    assert exec_module._coerce_tool_result([1, 2]) == {"value": [1, 2]}


def test_plan_node_tool_schema_handles_none_and_legacy_schema():
    assert plan_module._tool_schema(object()) == {}
    assert plan_module._tool_schema(
        type("Tool", (), {"args_schema": _LegacySchema()})()
    ) == {"properties": {"file_id": {"type": "string"}}}


async def _async_tool(file_id):
    return {"file_id": file_id}


def _sync_tool(file_id):
    return {"file_id": file_id}


class _BadTool:
    name = "bad"


def test_invoke_tool_supports_async_sync_and_rejects_non_invokable():
    assert asyncio.run(
        exec_module._invoke_tool(_async_tool, {"file_id": "file_a"})
    ) == {"file_id": "file_a"}
    assert asyncio.run(exec_module._invoke_tool(_sync_tool, {"file_id": "file_s"})) == {
        "file_id": "file_s"
    }

    try:
        asyncio.run(exec_module._invoke_tool(_BadTool(), {}))
    except TypeError as exc:
        assert "not invokable" in str(exc)
    else:
        raise AssertionError("expected TypeError")
