from __future__ import annotations

from pathlib import Path

_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "kafka_consumer_example.py"
)


def test_consumer_example_is_event_mesh_native_first():
    text = _EXAMPLE_PATH.read_text()

    assert "SAP Event Mesh native example" in text
    assert "MESSAGING_MODE=sap" in text
    assert "MESSAGING_MODE=local" in text
    assert text.index("MESSAGING_MODE=sap") < text.index(
        "MESSAGING_MODE=local"
    ), "Event Mesh production usage must appear before Kafka compatibility usage"


def test_consumer_example_mentions_event_mesh_urls_and_topics():
    text = _EXAMPLE_PATH.read_text()

    for needle in [
        "EVENT_MESH_REQUEST_TOPIC",
        "EVENT_MESH_MESSAGING_URL",
        "EVENT_MESH_MANAGEMENT_URL",
        "EVENT_MESH_TOKEN_URL",
        "EVENT_MESH_NAMESPACE",
    ]:
        assert needle in text, f"Missing Event Mesh example text: {needle}"
