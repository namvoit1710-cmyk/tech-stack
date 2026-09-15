"""SA-2056 (R9) — the feature auto-loads, config defaults are OFF/15, and the broker
is cross-wired after construction (it is NOT in available_dependencies)."""
from __future__ import annotations

from app.layer4_frameworks.config.app_config import Settings
from app.layer2_application.features import load_features, FEATURE_REGISTRY


def test_config_defaults_off_and_15():
    s = Settings()
    assert s.WORKER_HEALTH_FEED_ENABLED is False
    assert s.WORKER_HEALTH_FEED_INTERVAL_SECONDS == 15


def test_feature_is_discovered_by_the_loader():
    load_features()
    assert "worker_health_feed" in FEATURE_REGISTRY
    assert FEATURE_REGISTRY["worker_health_feed"].__name__ == "WorkerHealthFeedUseCase"


# ────────────── Cross-wire regression (Important — review finding) ──────────────
#
# bootstrap.py post-wires the broker onto the health-feed use case with a bare
# string-keyed dict lookup, outside ``available_dependencies`` (mirrors the R3
# ``worker_mirror_reconcile._repo`` wire just above it):
#
#     if "worker_health_feed" in container:
#         container["worker_health_feed"]._broker = infra.broker
#
# That key is load-bearing. If it's ever typo'd or the feature folder is
# renamed without updating this line, the ``if`` silently evaluates False:
# ``_broker`` stays ``None`` forever, ``execute()`` returns early on every
# tick, and the realtime feed is dead — with every other test in this file
# (and the rest of the suite) still green, because nothing else exercises
# this specific line.
#
# To exercise it for real we call build_app_container() with mocked settings
# (INFRA_MODE=mock, MESSAGING_MODE="", EVENT_MESH_CONFIG_SOURCE="") to avoid
# opening live HANA or Kafka connections. This test verifies that the actual
# cross-wire assignment executes and binds the broker correctly. If the key
# is renamed or removed, or placed in dead code, this test fails.


def test_cross_wire_sets_broker_from_infra_when_feature_present():
    """Calls real build_app_container() under mocked settings; verifies cross-wire
    binding of the health-feed broker actually executes via sentinel assertion.

    Sentinel approach: patches WorkerHealthFeedUseCase._broker to start with a
    distinctive value. If the cross-wire runs, _broker becomes infra.broker
    (None in mock mode). If it doesn't run, _broker stays the sentinel — test fails.
    """
    from unittest.mock import patch
    from app.layer4_frameworks.config.app_config import settings
    from bootstrap import build_app_container
    from app.layer2_application.features.worker_health_feed.use_cases.worker_health_feed_usecase import (
        WorkerHealthFeedUseCase,
    )

    # Sentinel: a distinctive string that could never be a real broker
    SENTINEL = "__UNWIRED_SENTINEL__"

    original_messaging_mode = settings.MESSAGING_MODE
    original_infra_mode = settings.INFRA_MODE
    original_em_config_source = settings.EVENT_MESH_CONFIG_SOURCE

    try:
        # Patch WorkerHealthFeedUseCase.__init__ to set _broker to sentinel instead of None.
        # This makes it possible to distinguish "never wired" from "wired to None".
        original_init = WorkerHealthFeedUseCase.__init__

        def patched_init(self, worker_registry_client=None, logger=None, **kwargs):
            original_init(self, worker_registry_client=worker_registry_client, logger=logger, **kwargs)
            self._broker = SENTINEL

        # Force mock mode with no broker so build_app_container runs cheaply
        settings.MESSAGING_MODE = ""
        settings.INFRA_MODE = "mock"
        settings.EVENT_MESH_CONFIG_SOURCE = ""

        with patch.object(WorkerHealthFeedUseCase, '__init__', patched_init):
            container = build_app_container()
            infra = container["_infra"]

            # Cross-wire proof: the _broker attribute must no longer be the sentinel.
            # It should equal infra.broker (None in mock mode).
            assert "worker_health_feed" in container, (
                "Feature not loaded into container. Check if deps are installed."
            )
            health_feed_uc = container["worker_health_feed"]

            # First assertion: _broker is NOT the sentinel (cross-wire ran)
            assert health_feed_uc._broker is not SENTINEL, (
                "Cross-wire did not execute: health-feed use case _broker is still the sentinel. "
                "Check if 'worker_health_feed' key is spelled correctly in bootstrap.py or if "
                "the cross-wire line is unreachable/dead."
            )

            # Second assertion: _broker is infra.broker (correct value)
            assert health_feed_uc._broker is infra.broker, (
                "Cross-wire failed to bind infra.broker correctly."
            )
    finally:
        settings.MESSAGING_MODE = original_messaging_mode
        settings.INFRA_MODE = original_infra_mode
        settings.EVENT_MESH_CONFIG_SOURCE = original_em_config_source


def test_cross_wire_is_a_noop_when_feature_missing():
    """Calls real build_app_container() when feature is disabled; verifies no error."""
    from app.layer4_frameworks.config.app_config import settings
    from bootstrap import build_app_container

    original_messaging_mode = settings.MESSAGING_MODE
    original_infra_mode = settings.INFRA_MODE
    original_em_config_source = settings.EVENT_MESH_CONFIG_SOURCE

    try:
        # Force mock mode to keep the test fast
        settings.MESSAGING_MODE = ""
        settings.INFRA_MODE = "mock"
        settings.EVENT_MESH_CONFIG_SOURCE = ""

        # Even if the feature is not in the container (skipped by smart-DI),
        # build_app_container() must not KeyError on the cross-wire guard.
        container = build_app_container()

        # If feature is present, verify it got the broker. If missing, that's OK too.
        if "worker_health_feed" in container:
            infra = container["_infra"]
            health_feed_uc = container["worker_health_feed"]
            assert health_feed_uc._broker is infra.broker
    finally:
        settings.MESSAGING_MODE = original_messaging_mode
        settings.INFRA_MODE = original_infra_mode
        settings.EVENT_MESH_CONFIG_SOURCE = original_em_config_source
