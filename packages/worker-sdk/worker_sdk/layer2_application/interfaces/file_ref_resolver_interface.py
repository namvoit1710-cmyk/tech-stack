from typing import Any, Protocol


class IFileRefResolver(Protocol):
    def resolve_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Walk *inputs* and resolve any ``__file_ref`` objects.

        Returns a **new** dict with file refs replaced by their resolved data.
        Raises on resolution failure.
        """
        ...
