"""Unit tests for the BFF silent token refresh (SA-2531)."""
import time

import pytest

from app.layer3_adapters.controllers.restful.v1 import auth_controller as ac
from app.layer4_frameworks.auth.oidc_client import OIDCClient
from app.layer4_frameworks.auth.session import Session


@pytest.mark.asyncio
async def test_me_refreshes_near_expiry(monkeypatch):
    async def fake_refresh(self, refresh_token):
        assert refresh_token == "RT1"
        return {"access_token": "AT2", "refresh_token": "RT2", "id_token": "IDT2", "expires_in": 300}

    monkeypatch.setattr(OIDCClient, "refresh", fake_refresh)
    s = Session(user={"id": "u"}, access_token="AT1", refresh_token="RT1", id_token="IDT1",
                expires_at=time.time() + 30, access_expires_at=time.time() + 10)
    await ac._refresh_if_needed(s)
    assert s.access_token == "AT2"
    assert s.refresh_token == "RT2"
    assert s.access_expires_at > time.time() + 200


@pytest.mark.asyncio
async def test_no_refresh_when_token_is_fresh(monkeypatch):
    calls = {"n": 0}

    async def fake_refresh(self, refresh_token):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(OIDCClient, "refresh", fake_refresh)
    s = Session(user={}, access_token="AT", refresh_token="RT", id_token=None,
                expires_at=time.time() + 3600, access_expires_at=time.time() + 3000)
    await ac._refresh_if_needed(s)
    assert calls["n"] == 0
    assert s.access_token == "AT"


@pytest.mark.asyncio
async def test_refresh_failure_keeps_current_session(monkeypatch):
    import httpx

    async def boom(self, refresh_token):
        raise httpx.ConnectError("kc down")

    monkeypatch.setattr(OIDCClient, "refresh", boom)
    s = Session(user={}, access_token="AT", refresh_token="RT", id_token=None,
                expires_at=time.time() + 30, access_expires_at=time.time() + 5)
    await ac._refresh_if_needed(s)  # must not raise
    assert s.access_token == "AT"
