from typing import Any, Protocol


class IDiagnosisCache(Protocol):
    """Port for the diagnosis cache (Software 5.0 A==B replay). Implemented in layer4
    (`DiagnosisCache`); layer2 depends only on this contract."""

    def get(self, signature: str) -> dict[str, Any] | None:
        """Return the cached verdict for an error signature, or None if unseen."""
        ...

    def put(self, signature: str, verdict: dict[str, Any]) -> None:
        """Remember a verdict so the next caller with the same signature replays it."""
        ...
