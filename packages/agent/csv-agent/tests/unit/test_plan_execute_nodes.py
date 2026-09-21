import asyncio
from types import SimpleNamespace

from app.layer2_application.graphs.nodes.execute_node import execute_node


class _AsyncTool:
    def __init__(self, name: str, result):
        self.name = name
        self.description = "test async tool"
        self._result = result

    async def ainvoke(self, tool_input):
        return self._result


class _SyncTool:
    def __init__(self, name: str, result: dict):
        self.name = name
        self.description = "test sync tool"
        self._result = result

    def invoke(self, tool_input):
        return self._result


def test_execute_node_marks_missing_tool_as_failed_step():
    state = {
        "execution_plan": {
            "goal": "test",
            "steps": [
                {"order": 1, "tool": "transform_column", "input": {}, "reason": "test"}
            ],
        },
        "parameters": {"file_id": "file_input_1"},
    }

    result = asyncio.run(execute_node(state, deps={"worker_tools": []}))

    assert result["execution_result"]["success"] is False
    assert result["execution_result"]["step_results"][0]["tool"] == "transform_column"
    assert "not found" in result["execution_result"]["step_results"][0]["error"].lower()
    assert result["agent_result"] == result["execution_result"]


def test_execute_node_updates_final_file_id_from_async_tool_result():
    tool = _AsyncTool("transform_column", {"file_id": "file_output_1", "message": "ok"})
    state = {
        "execution_plan": {
            "goal": "transform",
            "steps": [
                {
                    "order": 1,
                    "tool": "transform_column",
                    "input": {},
                    "reason": "transform",
                }
            ],
        },
        "parameters": {"file_id": "file_input_1"},
    }

    result = asyncio.run(
        execute_node(
            state,
            deps={
                "worker_tools": [tool],
                "settings": SimpleNamespace(AGENT_TYPE="csv-agent"),
            },
        )
    )

    assert result["execution_result"]["success"] is True
    assert result["execution_result"]["final_file_id"] == "file_output_1"
    assert result["parameters"]["file_id"] == "file_output_1"
    assert result["final_file_id"] == "file_output_1"


def test_execute_node_supports_sync_tool_invoke_fallback():
    tool = _SyncTool("query_file_data", {"message": "ok"})
    state = {
        "execution_plan": {
            "goal": "header",
            "steps": [
                {"order": 1, "tool": "query_file_data", "input": {}, "reason": "header"}
            ],
        },
        "parameters": {"file_id": "file_input_1"},
    }

    result = asyncio.run(execute_node(state, deps={"worker_tools": [tool]}))

    assert result["execution_result"]["success"] is True
    assert result["execution_result"]["step_results"][0]["tool"] == "query_file_data"


def test_execute_node_coerces_json_string_tool_result():
    tool = _AsyncTool("query_file_data", '{"file_id": "file_json", "message": "ok"}')
    state = {
        "execution_plan": {
            "goal": "query_file_data",
            "steps": [
                {
                    "order": 1,
                    "tool": "query_file_data",
                    "input": {},
                    "reason": "query_file_data",
                }
            ],
        },
        "parameters": {"file_id": "file_input_1"},
    }

    result = asyncio.run(execute_node(state, deps={"worker_tools": [tool]}))

    assert result["execution_result"]["step_results"][0]["result"]["message"] == "ok"
    assert result["final_file_id"] == "file_json"


def test_execute_node_records_tool_exception():
    class _Boom:
        name = "query_file_data"

        def invoke(self, tool_input):
            raise ValueError("bad input")

    state = {
        "execution_plan": {
            "goal": "query_file_data",
            "steps": [
                {
                    "order": 1,
                    "tool": "query_file_data",
                    "input": {},
                    "reason": "query_file_data",
                }
            ],
        },
        "parameters": {"file_id": "file_input_1"},
    }

    result = asyncio.run(execute_node(state, deps={"worker_tools": [_Boom()]}))

    assert result["execution_result"]["success"] is False
    assert "ValueError" in result["execution_result"]["step_results"][0]["error"]
