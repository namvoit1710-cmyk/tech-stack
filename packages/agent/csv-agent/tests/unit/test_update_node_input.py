import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_import_file_to_node_impl_calls_workflow_api_without_target_field():
    from app.layer2_application.features.import_file_to_node import (
        _import_file_to_node_impl,
    )

    gateway = SimpleNamespace(
        get_table_field_key=AsyncMock(return_value=None),
        assign_file_to_node=AsyncMock(
            return_value={
                "message": "Upload file successfully assigned to node input",
                "file_id": "file-3",
            }
        ),
    )

    result = asyncio.run(
        _import_file_to_node_impl(
            "run-1", "node-2", "file-3", workflow_node_gateway=gateway
        )
    )

    gateway.assign_file_to_node.assert_awaited_once_with(
        run_id="run-1",
        node_id="node-2",
        file_id="file-3",
        target_field=None,
    )
    assert result == {
        "message": "Upload file successfully assigned to node input",
        "file_id": "file-3",
    }


def test_import_file_to_node_impl_adds_target_field_when_table_wrapper():
    from app.layer2_application.features.import_file_to_node import (
        _import_file_to_node_impl,
    )

    gateway = SimpleNamespace(
        get_table_field_key=AsyncMock(return_value="items"),
        assign_file_to_node=AsyncMock(
            return_value={"message": "ok", "file_id": "file-3"}
        ),
    )

    asyncio.run(
        _import_file_to_node_impl(
            "run-1", "node-2", "file-3", workflow_node_gateway=gateway
        )
    )

    gateway.assign_file_to_node.assert_awaited_once_with(
        run_id="run-1",
        node_id="node-2",
        file_id="file-3",
        target_field="items",
    )
