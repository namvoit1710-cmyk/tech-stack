"""Callback publisher interface for workflow completion notifications."""

from __future__ import annotations

from typing import Protocol


class ICallbackPublisher(Protocol):
    """Publishes workflow completion callbacks to external services."""

    def publish(self, topic: str, message: dict, key: str = "") -> None: ...
