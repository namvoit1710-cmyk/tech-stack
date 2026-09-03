from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable

StructuredModelT = TypeVar("StructuredModelT")


@runtime_checkable
class ILLMService(Protocol):
    def get_chat_client(self) -> Any: ...

    def complete_with_format(
        self,
        *,
        messages: Sequence[Mapping[str, Any] | Any],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT: ...

    async def acomplete_with_format(
        self,
        *,
        messages: Sequence[Mapping[str, Any] | Any],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT: ...
