from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueueMetadata:
    queue_name: str = ""
    request_topic: str = ""
    reply_topic: str = ""
    delivery_hints: dict[str, Any] = field(default_factory=dict)
    request_message_type: str = ""
    response_message_type: str = ""
    request_topics: list[str] = field(default_factory=list)
    queue_names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.request_topics and self.request_topic:
            self.request_topics = [self.request_topic]
        if not self.request_topic and self.request_topics:
            self.request_topic = self.request_topics[0]
        if not self.queue_names and self.queue_name:
            self.queue_names = [self.queue_name]
        if not self.queue_name and self.queue_names:
            self.queue_name = self.queue_names[0]
