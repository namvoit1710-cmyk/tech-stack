from dataclasses import dataclass

from app.layer1_domain.value_objects.ai_role import AiRole


@dataclass
class AiMessage:
    """A single message in an AI conversation."""
    role: AiRole
    content: str
