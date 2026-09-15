"""Shared test fixtures for the Database Connection Worker.

Mirrors the house idiom (http-request-worker / mapping-data-worker conftest):
sync DummyLogger / DummyMonitor doubles injected into use-cases as MagicMock-free
fixtures. Logger methods accept **kwargs (structured logging); Monitor records tracks.
"""

import pytest


class DummyLogger:
    def info(self, message: str, **kwargs): pass
    def error(self, message: str, **kwargs): pass
    def warning(self, message: str, **kwargs): pass
    def debug(self, message: str, **kwargs): pass


class DummyMonitor:
    def __init__(self):
        self.tracked = []

    def track(self, metric: str, value: float):
        self.tracked.append((metric, value))


@pytest.fixture
def logger():
    return DummyLogger()


@pytest.fixture
def monitor():
    return DummyMonitor()
