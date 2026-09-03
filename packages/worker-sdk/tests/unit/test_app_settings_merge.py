"""Tests for the env > code > sdk precedence via _merge_app_settings_into_singleton."""

import os
from unittest.mock import patch

import pytest

from worker_sdk.layer4_frameworks.config.app_config import Settings
from worker_sdk.layer4_frameworks.config import app_config
from worker_sdk.runner import _merge_app_settings_into_singleton


@pytest.fixture
def preserved_singleton():
    """Snapshot the SDK singleton's fields before a test and restore after.

    Tests that mutate the singleton must leave it untouched for the next test.
    """
    snapshot = {
        name: getattr(app_config.settings, name)
        for name in type(app_config.settings).model_fields
    }
    yield
    for name, value in snapshot.items():
        setattr(app_config.settings, name, value)


class _WorkerScopedSettings(Settings):
    """Mimics a concrete worker's app/layer4_frameworks/config.py subclass."""

    APP_NAME: str = "Test Worker"
    WORKER_TYPE: str = "test_worker"
    WORKER_NAME: str = "Test Worker"
    WORKER_DESCRIPTION: str = "A subclass-provided worker description"
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "Wrench"
    WORKER_COLOR: str = "#123456"
    WORKER_TAGS: str = "test,subclass,worker"


class _ExtraFieldSettings(Settings):
    """Mimics a worker that ADDS new fields the SDK singleton doesn't have
    (e.g. the gateway-worker's ``CONTROL_PLANE_URL``)."""

    WORKER_TYPE: str = "extra_worker"
    CONTROL_PLANE_URL: str = "http://localhost:8001"  # subclass-only field
    RESOLVE_CACHE_TTL_S: float = 60.0  # subclass-only field


def test_merge_ignores_subclass_only_fields(preserved_singleton):
    """Regression: a worker may subclass ``Settings`` and add fields the SDK
    singleton lacks. The merge must copy only the singleton's own fields and
    must NOT raise ``'"Settings" object has no field "CONTROL_PLANE_URL"'``
    (the pre-fix behaviour, which blocked the gateway-worker from booting)."""
    app_settings = _ExtraFieldSettings()
    assert app_settings.CONTROL_PLANE_URL == "http://localhost:8001"

    # Pre-fix this raised ValueError on the first subclass-only field.
    _merge_app_settings_into_singleton(app_settings)

    # Shared field is merged onto the singleton …
    assert app_config.settings.WORKER_TYPE == "extra_worker"
    # … but the subclass-only field is NOT copied (the singleton never had it).
    assert not hasattr(app_config.settings, "CONTROL_PLANE_URL")


def test_merge_copies_subclass_defaults_onto_singleton(preserved_singleton):
    """Worker-supplied class defaults overwrite the SDK singleton (code > sdk)."""
    app_settings = _WorkerScopedSettings()
    assert app_settings.WORKER_TYPE == "test_worker"

    _merge_app_settings_into_singleton(app_settings)

    # The module-level singleton now reports the worker's subclass values.
    assert app_config.settings.APP_NAME == "Test Worker"
    assert app_config.settings.WORKER_TYPE == "test_worker"
    assert app_config.settings.WORKER_NAME == "Test Worker"
    assert app_config.settings.WORKER_DESCRIPTION == "A subclass-provided worker description"
    assert app_config.settings.WORKER_NODE_CLASS == "TECHNICAL"
    assert app_config.settings.WORKER_ICON == "Wrench"
    assert app_config.settings.WORKER_COLOR == "#123456"
    assert app_config.settings.get_tags_list() == ["test", "subclass", "worker"]


def test_env_still_wins_over_subclass_defaults(preserved_singleton):
    """With env var set, Pydantic gives env priority at Settings() time.

    The merge just copies the resulting value, so env > code > sdk holds
    end-to-end.
    """
    with patch.dict(os.environ, {"WORKER_TYPE": "from_env"}, clear=False):
        app_settings = _WorkerScopedSettings()
        assert app_settings.WORKER_TYPE == "from_env"  # Pydantic applied env override

        _merge_app_settings_into_singleton(app_settings)
        assert app_config.settings.WORKER_TYPE == "from_env"


def test_merge_preserves_sdk_defaults_for_non_overridden_fields(preserved_singleton):
    """Subclass fields that are NOT overridden still produce the SDK default,
    so the merge is a no-op for those."""
    app_settings = _WorkerScopedSettings()
    # WORKER_VERSION is not overridden on the subclass → base default "0.1.0"
    assert app_settings.WORKER_VERSION == "0.1.0"

    _merge_app_settings_into_singleton(app_settings)
    assert app_config.settings.WORKER_VERSION == "0.1.0"


def test_singleton_mutation_visible_through_cached_imports(preserved_singleton):
    """The merge mutates in place, so modules with a cached ``settings`` local
    see the new values — this is why we do not rebind the module attribute."""
    from worker_sdk.layer4_frameworks.config.app_config import settings as cached_ref

    assert cached_ref is app_config.settings
    app_settings = _WorkerScopedSettings()

    _merge_app_settings_into_singleton(app_settings)
    # Cached reference held before the merge reflects the updated field.
    assert cached_ref.WORKER_TYPE == "test_worker"
