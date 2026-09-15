"""Test fixtures for Email Worker."""

import pytest


class DummyLogger:
    """Dummy logger for testing."""

    def info(self, message: str, **kwargs):
        pass

    def error(self, message: str, **kwargs):
        pass

    def warning(self, message: str, **kwargs):
        pass

    def debug(self, message: str, **kwargs):
        pass


class DummyMonitor:
    """Dummy monitor for testing."""

    def __init__(self):
        self.tracked = []

    def track(self, metric: str, value: float):
        self.tracked.append((metric, value))


@pytest.fixture
def logger():
    """Provide a dummy logger for tests."""
    return DummyLogger()


@pytest.fixture
def monitor():
    """Provide a dummy monitor for tests."""
    return DummyMonitor()
