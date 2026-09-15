"""Shared fixtures for gateway-worker tests."""
import pytest


class DummyLogger:
    def info(self, message: str, **kwargs): pass
    def error(self, message: str, **kwargs): pass
    def warning(self, message: str, **kwargs): pass
    def debug(self, message: str, **kwargs): pass


@pytest.fixture
def logger():
    return DummyLogger()
