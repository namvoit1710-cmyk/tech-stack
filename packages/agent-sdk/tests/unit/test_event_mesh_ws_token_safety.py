"""Safety test: the AMQP-over-WebSocket failed-upgrade diagnostic must never
leak token material.

The diagnostic string is embedded in the ConnectionError raised on a failed
WebSocket upgrade (``_StdlibSyncWebSocket._handshake``), and that error is
logged upstream by the consumer/publisher loops. It must therefore reveal
nothing derived from the bearer token: not its contents, not its length, not
its JWT segment count. Only a generic "a Bearer header was sent" signal is safe.
"""

from __future__ import annotations

import os

from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp import (
    _stdlib_websocket as ws,
)

# A realistic-looking JWT: three dot-separated base64url segments.
_FAKE_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.c2lnbmF0dXJlLXZhbHVl"


def _clear_env() -> None:
    for name in (
        "EVENT_MESH_AMQP_WS_AUTHORIZATION",
        "EVENT_MESH_AMQP_WS_BEARER_TOKEN",
        "EVENT_MESH_AMQP_WS_AUTH_DIAGNOSTIC",
    ):
        os.environ.pop(name, None)
    ws.clear_amqp_ws_context()


def test_diagnostic_when_no_authorization() -> None:
    _clear_env()
    try:
        assert (
            ws._event_mesh_ws_authorization_diagnostic() == "authorization_sent=False"
        )
    finally:
        _clear_env()


def test_diagnostic_never_leaks_token_material() -> None:
    _clear_env()
    ws.set_amqp_ws_context(authorization=f"Bearer {_FAKE_JWT}")
    try:
        # The per-thread auth context is honored (header still built correctly)...
        assert ws._event_mesh_ws_authorization_header() == f"Bearer {_FAKE_JWT}"

        diagnostic = ws._event_mesh_ws_authorization_diagnostic()

        # ...but the diagnostic must confirm only that a Bearer header was sent.
        assert diagnostic == "authorization_sent=True auth_scheme=Bearer"

        # Explicitly assert no token-derived data leaks: no contents, no length,
        # no JWT-segment count.
        assert _FAKE_JWT not in diagnostic
        assert "token_len" not in diagnostic
        assert "jwt_parts" not in diagnostic
        assert str(len(_FAKE_JWT)) not in diagnostic
        # Segment count (3) must not appear as a standalone signal either.
        assert "3" not in diagnostic
    finally:
        _clear_env()


def test_diagnostic_appends_operator_configured_suffix_without_token_data() -> None:
    _clear_env()
    ws.set_amqp_ws_context(authorization=f"Bearer {_FAKE_JWT}")
    os.environ["EVENT_MESH_AMQP_WS_AUTH_DIAGNOSTIC"] = "region=eu10"
    try:
        diagnostic = ws._event_mesh_ws_authorization_diagnostic()
        assert diagnostic == "authorization_sent=True auth_scheme=Bearer region=eu10"
        assert "token_len" not in diagnostic
        assert "jwt_parts" not in diagnostic
        assert _FAKE_JWT not in diagnostic
    finally:
        _clear_env()
