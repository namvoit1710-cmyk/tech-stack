import importlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def test_node_catalog_v2_defaults_on(monkeypatch):
    """With ENABLE_NODE_CATALOG_V2 unset, the resolved flag is True (H cutover).

    Adaptation: the module singleton ``settings`` is a pydantic BaseSettings
    instance whose ``model_config`` loads a local ``.env`` file, and this repo's
    ``.env`` pins ``ENABLE_NODE_CATALOG_V2=true`` for local dev. That developer
    override would mask the code-level default and make this assertion pass
    regardless of the source change. To keep the INTENT — env (and file) unset
    => flag resolves True from the SOURCE default — we construct a fresh
    ``Settings(_env_file=None)`` that bypasses the ``.env`` override.
    """
    monkeypatch.delenv("ENABLE_NODE_CATALOG_V2", raising=False)
    from app.layer4_frameworks.config import app_config
    importlib.reload(app_config)
    try:
        resolved = app_config.Settings(_env_file=None).ENABLE_NODE_CATALOG_V2
        assert resolved is True
    finally:
        importlib.reload(app_config)  # restore module state for other tests


def test_legacy_sunset_date_is_in_the_future():
    from app.layer3_adapters.controllers.restful.v1.node_palette_controller import _SUNSET_DATE
    sunset = parsedate_to_datetime(_SUNSET_DATE)
    if sunset.tzinfo is None:
        sunset = sunset.replace(tzinfo=timezone.utc)
    assert sunset > datetime.now(timezone.utc)
