from agent_sdk.layer4_frameworks.config.app_config import Settings


def test_settings_has_routing_metadata_fields():
    """Settings should expose routing metadata fields with sensible defaults."""
    s = Settings()
    assert s.INPUT_SCHEMA == {}
    assert s.OUTPUT_SCHEMA == {}
    assert s.REQUIRED_PARAMETERS == []
    assert s.NEGATIVE_EXAMPLES == []
    assert s.ROUTING_TIMEOUT_SECONDS == 300.0
    assert s.AGENT_KIND == "SERVICE"
    assert s.IS_PUBLISHED is True
    assert s.ATTACHED_AGENT_IDS == []
    assert s.SYSTEM_PROMPT == ""
    assert s.MAX_CONCURRENCY == 1
    assert s.QUEUE_NAME == ""
    assert s.QUEUE_REQUEST_TOPIC == ""
    assert s.QUEUE_REPLY_TOPIC == ""
    assert s.QUEUE_DELIVERY_HINTS == {}


def test_settings_routing_metadata_overridable():
    """Sub-agent settings can override routing metadata via env or subclass."""

    class MySettings(Settings):
        INPUT_SCHEMA: dict = {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
        }
        REQUIRED_PARAMETERS: list = ["file_id"]
        NEGATIVE_EXAMPLES: list = ["general questions"]

    s = MySettings()
    assert s.INPUT_SCHEMA["properties"]["file_id"]["type"] == "string"
    assert s.REQUIRED_PARAMETERS == ["file_id"]
    assert s.NEGATIVE_EXAMPLES == ["general questions"]


def test_settings_registry_contract_fields_overridable():
    class MySettings(Settings):
        AGENT_KIND: str = "SUPERVISOR"
        IS_PUBLISHED: bool = False
        ATTACHED_AGENT_IDS: list[str] = ["agent-a", "agent-b"]
        SYSTEM_PROMPT: str = "Route requests carefully"
        MAX_CONCURRENCY: int = 4
        QUEUE_NAME: str = "planner.queue"
        QUEUE_REQUEST_TOPIC: str = "planner.request"
        QUEUE_REPLY_TOPIC: str = "planner.reply"
        QUEUE_DELIVERY_HINTS: dict = {"priority": "high"}

    s = MySettings()

    assert s.AGENT_KIND == "SUPERVISOR"
    assert s.IS_PUBLISHED is False
    assert s.ATTACHED_AGENT_IDS == ["agent-a", "agent-b"]
    assert s.SYSTEM_PROMPT == "Route requests carefully"
    assert s.MAX_CONCURRENCY == 4
    assert s.QUEUE_NAME == "planner.queue"
    assert s.QUEUE_REQUEST_TOPIC == "planner.request"
    assert s.QUEUE_REPLY_TOPIC == "planner.reply"
    assert s.QUEUE_DELIVERY_HINTS == {"priority": "high"}
