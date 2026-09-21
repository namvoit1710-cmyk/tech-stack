from __future__ import annotations

from typing import Any, Callable, Mapping, TypeVar, cast

T = TypeVar("T")


def ensure_definition(
    value: object,
    *,
    definition_type: type[T],
    factory: Callable[[Mapping[str, Any] | None], T],
) -> T:
    if isinstance(value, definition_type):
        return value
    if isinstance(value, Mapping):
        return factory(cast(Mapping[str, Any], value))
    return factory(None)
