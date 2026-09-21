from typing import Protocol

from app.layer1_domain.entities.ui_event import UIEvent


class IChatTimelineNodeRepository(Protocol):
    def upsert_from_event(self, event: UIEvent) -> None: ...
