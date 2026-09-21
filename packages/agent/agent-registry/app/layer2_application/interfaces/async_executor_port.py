"""Async executor interface"""
from typing import Callable, TypeVar, Protocol

T = TypeVar('T')

class AsyncExecutorInterface(Protocol):
    """Interface for executing synchronous functions in an asynchronous context."""
    
    async def run_sync(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Run a synchronous function in an asynchronous context.
        note:
            This method should be implemented using an appropriate async execution strategy,
            such as anyio.to_thread or asyncio.to_thread, depending on the application's needs.
        """
        ...