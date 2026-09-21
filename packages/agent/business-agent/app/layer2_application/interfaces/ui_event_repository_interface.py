from typing import Protocol

from app.layer1_domain.entities.ui_event import UIEvent


class IUIEventRepository(Protocol):
    def save_event(self, event: UIEvent) -> None: ...
