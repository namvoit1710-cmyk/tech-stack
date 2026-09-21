from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenProviderPort(Protocol):
    async def get_token(self) -> str:
        """Fetch a token. Raises UnauthorizedException if unavailable."""
        ...
