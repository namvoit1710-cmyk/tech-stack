from __future__ import annotations

from typing import Callable, Optional


class NodeTypeRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, Callable] = {}

    def register(self, type_name: str, fn: Callable) -> None:
        self._registry[type_name] = fn

    def get(self, type_name: str) -> Optional[Callable]:
        return self._registry.get(type_name)

    def has(self, type_name: str) -> bool:
        return type_name in self._registry

    def list_types(self) -> list[str]:
        return sorted(self._registry.keys())
