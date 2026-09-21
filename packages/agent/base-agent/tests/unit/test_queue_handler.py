import asyncio

from app.layer2_application.queues.workflow_queue_handler import handle_workflow_queue_message


def test_handle_workflow_queue_message_returns_empty_dict_for_current_stub():
    result = asyncio.run(handle_workflow_queue_message())

    assert result == {}