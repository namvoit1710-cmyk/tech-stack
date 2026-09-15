from typing import Any, Protocol


class IResolverStrategy(Protocol):
    """Strategy for resolving a variable path against source data.

    Implementations may perform I/O (e.g., downloading a file-backed
    reference from the file service), so ``try_resolve`` is ``async``.
    Pure in-memory strategies can still implement it as ``async def``
    without awaiting anything.
    """

    async def try_resolve(
        self,
        source_data: Any,
        path_parts: tuple[str | int, ...],
    ) -> Any: ...
