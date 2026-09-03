import importlib.util
from typing import get_args, get_origin, get_type_hints

from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)
from agent_sdk.layer2_application.services.workflow_event_emitter import (
    WorkflowEventEmitter,
)
from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
    BufferedPushGatewayNotifier,
)
from agent_sdk.layer4_frameworks.messaging.grpc_push_gateway_notifier import (
    GrpcPushGatewayNotifier,
)
from agent_sdk.layer4_frameworks.messaging.http_push_gateway_notifier import (
    HttpPushGatewayNotifier,
)


def test_layer4_push_gateway_notifier_protocol_shim_removed():
    assert (
        importlib.util.find_spec(
            "agent_sdk.layer4_frameworks.messaging.push_gateway_notifier_protocol"
        )
        is None
    )


def test_push_gateway_notifier_protocol_is_runtime_checkable():
    http_notifier = HttpPushGatewayNotifier("http://localhost:8000")
    grpc_notifier = GrpcPushGatewayNotifier("localhost:50051")
    buffered_notifier = BufferedPushGatewayNotifier(inner=http_notifier)

    assert isinstance(http_notifier, PushGatewayNotifierProtocol)
    assert isinstance(grpc_notifier, PushGatewayNotifierProtocol)
    assert isinstance(buffered_notifier, PushGatewayNotifierProtocol)


def test_workflow_event_emitter_uses_push_gateway_notifier_protocol_annotation():
    hints = get_type_hints(WorkflowEventEmitter.__init__)
    annotation = hints["push_gateway_notifier"]

    assert get_origin(annotation) is not None
    assert PushGatewayNotifierProtocol in get_args(annotation)
