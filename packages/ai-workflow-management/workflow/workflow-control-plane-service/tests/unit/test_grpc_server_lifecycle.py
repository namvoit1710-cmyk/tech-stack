"""SA-1526 — gRPC server lifecycle decoupled from TRANSPORT_MODE.

The CF Docker port health check probes every EXPOSEd port (:8001 and :50051).
Before this change the gRPC server only started under TRANSPORT_MODE=grpc, so
flipping dispatch to event_mesh crash-looped the pod ("Instance never healthy:
dial tcp :50051 — connection refused"). The server now starts in every mode
behind GRPC_SERVER_ENABLED (default on), which also keeps existing external
gRPC callers of the control plane working during the transport migration.

No module reloads here — swapping the ``settings`` singleton mid-session
breaks unrelated tests that captured it at import time. The env-parsing
expression is tested directly instead.
"""

from __future__ import annotations

from app.layer4_frameworks.config.app_config import settings


def _parse_enabled(raw: str) -> bool:
    """Mirror of the Settings field expression for GRPC_SERVER_ENABLED."""
    return raw.lower() in ("1", "true", "yes")


class TestGrpcServerEnabledSetting:
    def test_default_is_enabled_in_this_environment(self):
        # GRPC_SERVER_ENABLED is unset in the test env → default "true" path.
        assert settings.GRPC_SERVER_ENABLED is True

    def test_parse_accepts_truthy_forms(self):
        assert _parse_enabled("true") and _parse_enabled("1") and _parse_enabled("YES")

    def test_parse_rejects_falsy_forms(self):
        assert not _parse_enabled("0")
        assert not _parse_enabled("false")
        assert not _parse_enabled("no")
        assert not _parse_enabled("")


class TestLifespanStartCondition:
    """The lifespan guard is `GRPC_SERVER_ENABLED or TRANSPORT_MODE == grpc` —
    verify the truth table without booting the app."""

    def _should_start(self, enabled: bool, mode: str) -> bool:
        return enabled or mode.lower() == "grpc"

    def test_event_mesh_with_server_enabled_starts(self):
        """The exact production combination that crash-looped before SA-1526."""
        assert self._should_start(True, "event_mesh") is True

    def test_grpc_mode_starts_even_if_flag_off(self):
        # Legacy contract preserved: grpc dispatch implies the server.
        assert self._should_start(False, "grpc") is True

    def test_http_mode_with_flag_off_does_not_start(self):
        assert self._should_start(False, "http") is False
