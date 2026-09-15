from fastapi import APIRouter, Request, Response
from typing import Any

from app.layer4_frameworks.config.app_config import settings

router = APIRouter()


@router.get("")
def health_check() -> Any:
    return {"status": "ok"}


@router.get("/readiness")
def readiness_check(request: Request, response: Response) -> Any:
    """SA-1466 — readiness probe that surfaces the per-instance response-topic
    listener state.

    Always reports the listener's ``degraded`` flag. When
    ``RESPONSE_LISTENER_READINESS_GATE_ENABLED`` is set (multi-pod
    deployments), a degraded listener flips the response to HTTP 503 so the
    platform pulls this pod from rotation instead of letting it silently drop
    cross-instance EditNodeDataAction responses. Default off → always 200,
    behaviour unchanged for single-pod deployments.
    """
    container = request.app.state.container
    listener = container.get("_response_topic_listener")
    listener_health = listener.health() if listener is not None else {"present": False}

    reasons: list[str] = []
    ready = True
    gate_on = settings.RESPONSE_LISTENER_READINESS_GATE_ENABLED
    if gate_on and listener is not None and listener.degraded:
        ready = False
        reasons.append("response_topic_listener_degraded")

    # SA-1470 — surface the Event Mesh consume transport; when
    # EVENT_MESH_REQUIRE_AMQP is set, a pod forced onto the REST fallback
    # reports NotReady instead of consuming silently degraded. Default off.
    consume_state = None
    infra = container.get("_infra")
    broker = getattr(infra, "broker", None) if infra is not None else None
    if broker is not None and hasattr(broker, "consume_transport_state"):
        consume_state = broker.consume_transport_state()
    require_amqp = getattr(settings, "EVENT_MESH_REQUIRE_AMQP", False)
    if require_amqp and consume_state is not None and consume_state.get("rest_fallback"):
        ready = False
        reasons.append("event_mesh_rest_fallback")

    # A topic stood down because a PEER holds its queue is not a fault: the
    # work is being consumed, just not here, and that is the intended shape
    # while the control plane allows one consumer per queue. Reporting it as
    # NotReady would mark every pod but one as broken the moment someone
    # scales. The other two reasons mean this pod genuinely cannot consume.
    if require_amqp and consume_state is not None:
        broken = {
            topic: reason
            for topic, reason in (consume_state.get("stood_down") or {}).items()
            if reason != "peer_owns_queue"
        }
        if broken:
            ready = False
            reasons.append(f"event_mesh_standdown:{sorted(set(broken.values()))}")

    if not ready:
        response.status_code = 503

    return {
        "ready": ready,
        "reasons": reasons,
        "readiness_gate_enabled": gate_on,
        "response_topic_listener": listener_health,
        "event_mesh_consume": consume_state,
        "event_mesh_require_amqp": require_amqp,
    }


@router.get("/eventmesh")
def eventmesh_health(request: Request) -> Any:
    """Report internal state of the Event Mesh publisher (async flusher + circuit + token).

    Returns 200 in all cases — use the ``healthy`` field or circuit state to
    drive alerting. Returns a minimal payload when Event Mesh is not configured.
    """
    infra = request.app.state.container.get("_infra")
    if infra is None or infra.broker is None:
        return {"configured": False}
    if not hasattr(infra.broker, "health_check"):
        return {"configured": True, "supported": False}
    snapshot = infra.broker.health_check()
    return {"configured": True, "supported": True, **snapshot}
