from typing import Any, Protocol


class ILLMProvider(Protocol):
    def complete_with_format(self, *, messages: list[dict[str, str]], response_model: type[Any]) -> Any: ...
