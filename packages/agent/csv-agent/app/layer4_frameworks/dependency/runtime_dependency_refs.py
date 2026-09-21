from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class RuntimeDependencyRef(Generic[T]):
    """Per-container deferred dependency reference.

    Some concrete adapters are created by agent-sdk after local tools are built
    (for example the SDK LLM service and MCP-backed Data Factory gateway). This
    explicit reference keeps those dependencies constructor-injected into tools
    without falling back to module-global service locators.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._value: T | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return self._value is not None

    def set(self, value: T) -> None:
        self._value = value

    def require(self) -> T:
        if self._value is None:
            raise RuntimeError(f"{self._name} dependency is not configured")
        return self._value
