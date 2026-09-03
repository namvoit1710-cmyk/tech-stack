import os
from unittest.mock import patch

from worker_sdk.layer4_frameworks.config.app_config import Settings


def test_settings_defaults():
    """Settings has correct defaults when no env vars are set."""
    s = Settings()
    assert s.APP_NAME == "Worker SDK"
    assert s.APP_MODE == "SERVER"
    assert s.SDK_VERSION == "1.0.0"
    assert s.WORKER_TYPE == "generic"
    assert s.WORKER_VERSION == "0.1.0"
    assert s.REGISTRY_URL == "http://localhost:8000"
    assert s.HEARTBEAT_INTERVAL_SECONDS == 30
    assert s.SERVER_HOST == "localhost"
    assert s.SERVER_PORT == 35000
    assert s.DATA_INPUT_PATH == "/tmp/worker/input"
    assert s.DATA_OUTPUT_PATH == "/tmp/worker/output"


def test_settings_from_env():
    """Settings reads values from environment variables."""
    env = {
        "APP_NAME": "Test Worker",
        "APP_MODE": "HEADLESS",
        "WORKER_TYPE": "custom",
        "WORKER_VERSION": "2.0.0",
        "REGISTRY_URL": "http://registry:9000",
        "HEARTBEAT_INTERVAL_SECONDS": "60",
        "SERVER_HOST": "127.0.0.1",
        "SERVER_PORT": "9090",
        "DATA_INPUT_PATH": "/data/in",
        "DATA_OUTPUT_PATH": "/data/out",
    }
    with patch.dict(os.environ, env, clear=False):
        s = Settings()
        assert s.APP_NAME == "Test Worker"
        assert s.APP_MODE == "HEADLESS"
        assert s.WORKER_TYPE == "custom"
        assert s.WORKER_VERSION == "2.0.0"
        assert s.REGISTRY_URL == "http://registry:9000"
        assert s.HEARTBEAT_INTERVAL_SECONDS == 60
        assert s.SERVER_HOST == "127.0.0.1"
        assert s.SERVER_PORT == 9090
        assert s.DATA_INPUT_PATH == "/data/in"
        assert s.DATA_OUTPUT_PATH == "/data/out"
