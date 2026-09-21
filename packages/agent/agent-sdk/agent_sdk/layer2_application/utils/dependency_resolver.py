from __future__ import annotations

from typing import Any, Mapping, Protocol, cast


class DependencyResolverInterface(Protocol):
    def get(self, name: str, default: Any = None) -> Any: ...


class DependencyResolver:
    def __init__(self, value: Mapping[str, Any] | None = None):
        self._value = dict(value or {})

    def get(self, name: str, default: Any = None) -> Any:
        return self._value.get(name, default)

    def __getattr__(self, name: str) -> Any:
        return self._value.get(name)


def ensure_dependency_resolver(
    value: DependencyResolverInterface | Mapping[str, Any] | None,
) -> DependencyResolverInterface:
    if value is None:
        return DependencyResolver(None)
    if isinstance(value, DependencyResolver):
        return value
    if isinstance(value, Mapping):
        return DependencyResolver(cast(Mapping[str, Any], value))
    return value
