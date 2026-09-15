"""SA-1551 — worker-function invoke proxy resolves a design-time credential_ref.

The FE serverOptions POST body carries {params:{args:{credential_ref,...}}}. The proxy
must resolve that ref against the vault (tenant-scoped), fold it into
params.__credentials, and forward to the worker function — without ever returning the
secret. Errors map to 403/404/502/503. When no credential_ref is present, the body is
forwarded unchanged (gateway path parity).
"""
import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container


class _FakeRegistryClient:
    """Captures the params forwarded to the worker function."""

    def __init__(self):
        self.last_params = None

    def invoke_worker_function(self, worker_type, function_name, params):
        self.last_params = params
        return {"status": "ok", "options": [{"id": "t1", "displayName": "Gov"}]}


class _FakeVault:
    def __init__(self, entry=None, exc=None):
        self._entry = entry or {"type": "json", "config": {"tokenUrl": "https://t", "clientId": "cid", "clientSecret": "sec"}}
        self._exc = exc
        self.calls = []

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.calls.append((tenant_id, credential_id, user_id))
        if self._exc:
            raise self._exc
        return self._entry


class _CredForbidden(Exception):
    pass


class _CredMissing(Exception):
    pass


# rename to the exact class names the controller matches on
_CredForbidden.__name__ = "CredentialResolveAuthError"
_CredMissing.__name__ = "CredentialNotFound"


def _client(vault, registry):
    container = build_app_container()
    container["_worker_registry_client"] = registry
    container["_vault_credential_client"] = vault
    return TestClient(create_fastapi_app(container))


INVOKE = "/api/v1/workers/ms_teams_channel/functions/list_teams/invoke"


def test_resolves_credential_ref_into_params():
    vault = _FakeVault()
    reg = _FakeRegistryClient()
    client = _client(vault, reg)
    resp = client.post(INVOKE, json={"params": {"args": {"credential_ref": "cred-1"}}, "tenant_id": "tenant-9"})
    assert resp.status_code == 200
    # tenant-scoped resolve happened
    assert vault.calls == [("tenant-9", "cred-1", None)]
    # __credentials folded in for the worker function; keyed by the ref
    creds = reg.last_params["__credentials"]
    assert creds["cred-1"]["type"] == "json"
    assert creds["cred-1"]["config"]["clientId"] == "cid"


def test_secret_never_returned_to_caller():
    vault = _FakeVault(entry={"type": "bearer", "config": {"token": "TOP-SECRET"}})
    reg = _FakeRegistryClient()
    client = _client(vault, reg)
    resp = client.post(INVOKE, json={"params": {"args": {"credential_ref": "cred-1"}}})
    assert resp.status_code == 200
    assert "TOP-SECRET" not in resp.text


def test_no_credential_ref_forwards_body_unchanged():
    vault = _FakeVault()
    reg = _FakeRegistryClient()
    client = _client(vault, reg)
    resp = client.post(INVOKE, json={"params": {"function_id": "list_teams"}})
    assert resp.status_code == 200
    assert vault.calls == []  # no resolve attempted
    assert "__credentials" not in reg.last_params


def test_forbidden_credential_maps_403():
    vault = _FakeVault(exc=_CredForbidden("nope"))
    client = _client(vault, _FakeRegistryClient())
    resp = client.post(INVOKE, json={"params": {"args": {"credential_ref": "cred-x"}}})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CREDENTIAL_FORBIDDEN"


def test_missing_credential_maps_404():
    vault = _FakeVault(exc=_CredMissing("gone"))
    client = _client(vault, _FakeRegistryClient())
    resp = client.post(INVOKE, json={"params": {"args": {"credential_ref": "cred-x"}}})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_vault_unconfigured_fails_closed_503():
    client = _client(None, _FakeRegistryClient())
    resp = client.post(INVOKE, json={"params": {"args": {"credential_ref": "cred-1"}}})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "VAULT_UNAVAILABLE"


def test_generic_resolve_failure_maps_502_without_detail():
    vault = _FakeVault(exc=RuntimeError("connreset to https://vault.internal/secret"))
    client = _client(vault, _FakeRegistryClient())
    resp = client.post(INVOKE, json={"params": {"args": {"credential_ref": "cred-1"}}})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "CREDENTIAL_RESOLVE_FAILED"
    assert "vault.internal" not in resp.text  # no sensitive detail echoed
