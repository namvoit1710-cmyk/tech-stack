from __future__ import annotations

from typing import Any

import pytest

from app.layer2_application.graphs.nodes.execute_node import execute_node


class _ArgsSchema:
    @staticmethod
    def schema() -> dict[str, Any]:
        return {
            "properties": {
                "file_id": {"type": "string"},
                "odata_query": {"type": "string"},
                "top": {"type": "integer"},
            }
        }


class _Tool:
    name = "query_file_data"
    args_schema = _ArgsSchema()

    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payload = payload
        return {"file_id": payload["file_id"], "data": [{"id": 1}], "read_only": True}


@pytest.mark.asyncio
async def test_execute_node_filters_payload_by_sdk_tool_schema() -> None:
    tool = _Tool()

    result = await execute_node(
        {
            "parameters": {
                "file_id": "file_550e8400-e29b-41d4-a716-446655440000",
                "conversation_id": "conv-1",
                "unused": "must be filtered",
            },
            "execution_plan": {
                "goal": "query",
                "steps": [
                    {
                        "order": 1,
                        "tool": "query_file_data",
                        "input": {"odata_query": "$top=1"},
                        "reason": "read only",
                    }
                ],
            },
        },
        {"worker_tools": [tool]},
    )

    assert tool.payload == {
        "file_id": "file_550e8400-e29b-41d4-a716-446655440000",
        "odata_query": "$top=1",
    }
    assert result["execution_result"]["success"] is True
    assert result["final_file_id"] == "file_550e8400-e29b-41d4-a716-446655440000"
