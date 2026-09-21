from __future__ import annotations


class TransportSource:
    EVENT_MESH = "event_mesh"
    KAFKA = "kafka"
    MOCK = "mock"
    BROKER = "broker"

    @staticmethod
    def normalize(value: object, *, default: str = BROKER) -> str:
        source = str(value or "").strip().lower().replace("-", "_")
        if source in {"sap", "eventmesh", "sap_event_mesh", "event_mesh"}:
            return TransportSource.EVENT_MESH
        if source in {"local", "kafka"}:
            return TransportSource.KAFKA
        if source in {"mock", "memory", "in_memory"}:
            return TransportSource.MOCK
        return source or default
