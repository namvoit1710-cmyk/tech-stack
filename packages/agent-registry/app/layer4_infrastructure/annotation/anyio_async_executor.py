"""Async wrapper for sync functions in the application layer"""
from anyio import to_thread
from app.layer2_application.interfaces.async_executor_port import AsyncExecutorInterface, T
from typing import Callable
from functools import partial

class AsyncExecutor(AsyncExecutorInterface):
    """Utility class to wrap synchronous functions in asynchronous context using anyio.to_thread"""

    async def run_sync(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Run a synchronous function in an asynchronous context using anyio.to_thread."""
        if kwargs:
            return await to_thread.run_sync(partial(func, *args, **kwargs))
        return await to_thread.run_sync(func, *args)