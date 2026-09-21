# from __future__ import annotations

# from unittest.mock import AsyncMock

# import pytest

# from app.layer1_domain.entities.file_state import ConversationTurn
# from app.layer2_application.features.execute_agent.nodes.plan_execute_nodes import (
#     create_plan_node,
# )


# class _Tool:
#     def __init__(self, description: str) -> None:
#         self.description = description


# @pytest.mark.asyncio
# async def test_create_plan_node_adds_revert_rule_to_avoid_import_step() -> None:
#     open_ai_service = type("MockOpenAIService", (), {})()
#     open_ai_service.get_chat_completion = AsyncMock(
#         return_value='{"goal": "Revert file", "steps": [{"order": 1, "tool": "revert_file", "input": {"revert_instruction": "restore previous version"}, "reason": "restore previous version"}]}'
#     )

#     state = {
#         "message": "Revert the current table to the previous old file version.",
#         "parameters": {
#             "file_id": "current-file.csv",
#             "run_id": "run-123",
#             "node_id": "node-456",
#         },
#         "conversation_history": [
#             ConversationTurn(
#                 role="system",
#                 message="Done! Download the updated file here: https://example.com/api/v1/download/current-file.csv",
#                 file_id="current-file.csv",
#             )
#         ],
#     }
#     deps = {
#         "open_ai_service": open_ai_service,
#         "tool_registry": {
#             "revert_file": _Tool("Revert to an older file version from conversation history."),
#             "import_file_to_node": _Tool("Attach or assign a file to a workflow node. Not for reverting to a previous file version."),
#         },
#     }

#     result = await create_plan_node(state, deps)

#     assert result["execution_plan"]["steps"][0]["tool"] == "revert_file"

#     prompt, user_prompt = open_ai_service.get_chat_completion.await_args.args
#     assert "For revert, undo, turn back, restore previous version, or restore old file requests, use revert_file only" in prompt
#     assert "Do NOT add import_file_to_node just because run_id or node_id is available in parameters." in prompt
#     assert user_prompt == "Revert the current table to the previous old file version."