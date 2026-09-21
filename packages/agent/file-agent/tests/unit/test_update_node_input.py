# from types import SimpleNamespace
# from unittest.mock import AsyncMock, MagicMock

# import pytest


# @pytest.mark.asyncio
# async def test_import_file_to_node_impl_calls_workflow_api(monkeypatch):
#     from app.layer2_application.features.import_file_to_node import _import_file_to_node_impl

#     response = MagicMock()
#     response.status_code = 200
#     response.json.return_value = {"data": {"updated": True}}
#     response.raise_for_status = MagicMock()

#     client = MagicMock()
#     client.patch = AsyncMock(return_value=response)

#     async_client = MagicMock()
#     async_client.__aenter__ = AsyncMock(return_value=client)
#     async_client.__aexit__ = AsyncMock(return_value=None)

#     monkeypatch.setattr(
#         "app.layer2_application.features.import_file_to_node.AppSettingsAdapter",
#         lambda: SimpleNamespace(WORKFLOW_API_BASE_URL="https://workflow.example/"),
#     )
#     monkeypatch.setattr(
#         "app.layer2_application.features.import_file_to_node.httpx.AsyncClient",
#         MagicMock(return_value=async_client),
#     )

#     result = await _import_file_to_node_impl("run-1", "node-2", "file-3")

#     client.patch.assert_awaited_once_with(
#         "https://workflow.example/runs/run-1/nodes/node-2/input",
#         headers={
#             "Content-Type": "application/json",
#             "x-tenant-id": "system",
#             "user-agent": "file-agent/1.0",
#         },
#         json={
#             "auto_rerun": False,
#             "file_id": "file-3",
#         },
#     )
#     response.raise_for_status.assert_called_once_with()
#     assert result == {"status_code": 200, "data": {"updated": True}}


# def test_build_app_container_registers_import_file_to_node(monkeypatch):
#     import bootstrap

#     captured = {}

#     monkeypatch.setattr(bootstrap, "OpenAIService", lambda: object())
#     monkeypatch.setattr(bootstrap, "AgentGraphBuilder", lambda state_schema: object())
#     monkeypatch.setattr(bootstrap, "create_checkpointer", lambda settings: object())
#     monkeypatch.setattr(bootstrap, "build_graph", lambda deps: captured.setdefault("deps", deps) or object())
#     monkeypatch.setattr(bootstrap, "_sdk_build_app_container", lambda agent_graph: {"agent_graph": agent_graph})

#     bootstrap.build_app_container()

#     assert "import_file_to_node" in captured["deps"]["tool_registry"]