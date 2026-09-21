import asyncio

from app.layer4_frameworks.clients.workflow_clients import WorkflowClient


def test_trigger_workflow_returns_mock_payload_when_base_url_is_unset():
    client = WorkflowClient()

    result = asyncio.run(client.trigger_workflow("wf-client-1"))

    assert result == {"status": "mock", "workflow_id": "wf-client-1"}