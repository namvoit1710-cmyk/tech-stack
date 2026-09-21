from dataclasses import dataclass

from app.layer1_domain.entities.base_entity import BaseEntity

from app.layer1_domain.constants.default_constant import (
    DEFAULT_QUEUE_NAME,
    DEFAULT_QUEUE_TYPE,
)


@dataclass
class QueueDefinition(BaseEntity):
    queue_name: str = DEFAULT_QUEUE_NAME
    queue_type: str = DEFAULT_QUEUE_TYPE
