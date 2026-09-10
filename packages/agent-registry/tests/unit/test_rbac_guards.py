"""T3 slice 1 — guard/DI foundation unit tests.

Covers get_current_principal (token pass-through), require_permission (allow / 403 /
is_super + system bypass / multi-code), MeResponse projection, the ForbiddenException
→ 403 mapping, and a boot smoke that GET /me is registered. No DB, no network.
"""
import asyncio
import os
from types import SimpleNamespace

# Settings() has required fields; set dummies before importing anything that builds it
# (container is imported transitively by the guard module).
os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ.setdefault("DATABASE_SCHEMA", "PUBLIC")

import pytest

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import DomainException, ForbiddenException
from app.layer3_presentation.dependencies.principal import (
    get_bearer_token,
    get_current_principal,
    require_permission,
)
from app.layer3_presentation.schemas.principal_schema import MeResponse


class _FakeUC:
    def __init__(self, principal):
        self.principal = principal
        self.token = "UNSET"

    async def execute(self, token):
        self.token = token
        return self.principal


def _req(token):
    return SimpleNamespace(state=SimpleNamespace(jwt_token=token))


def _run(coro):
    return asyncio.run(coro)


def _user(perms, is_super=False, role="user"):
    return Principal.for_user(
        user_id="u1", external_id="e1", role_code=role, is_super=is_super,
        permissions=frozenset(perms),
    )


# ---- get_bearer_token / get_current_principal ------------------------------
def test_get_bearer_token_reads_request_state():
    assert get_bearer_token(_req("tok-9")) == "tok-9"
    assert get_bearer_token(_req(None)) is None


def test_get_current_principal_passes_token_and_returns_principal():
    p = _user({"agent.read"})
    uc = _FakeUC(p)
    result = _run(get_current_principal(token="tok-123", use_case=uc))
    assert result is p
    assert uc.token == "tok-123"


def test_get_current_principal_forwards_none_token():
    p = Principal.system()
    uc = _FakeUC(p)
    result = _run(get_current_principal(token=None, use_case=uc))
    assert result is p and uc.token is None


# ---- require_permission ----------------------------------------------------
def test_require_permission_allows_when_holding_all():
    dep = require_permission("role.read", "permission.view")
    p = _user({"role.read", "permission.view"})
    assert _run(dep(principal=p)) is p


def test_require_permission_forbids_when_missing():
    dep = require_permission("role.create")
    with pytest.raises(ForbiddenException):
        _run(dep(principal=_user({"role.read"})))


def test_require_permission_is_super_bypasses():
    dep = require_permission("role.create", "permission.view")
    p = _user(set(), is_super=True, role="super_admin")
    assert _run(dep(principal=p)) is p


def test_require_permission_system_bypasses():
    dep = require_permission("role.create")
    p = Principal.system()
    assert _run(dep(principal=p)) is p


def test_require_permission_reports_missing_codes():
    dep = require_permission("role.read", "role.create")
    with pytest.raises(ForbiddenException) as ei:
        _run(dep(principal=_user({"role.read"})))
    assert "role.create" in str(ei.value)
    assert "role.read" not in str(ei.value)  # held one is not reported missing


def test_require_permission_with_no_codes_allows_any_principal():
    dep = require_permission()  # authentication-only gate
    p = _user(set())
    assert _run(dep(principal=p)) is p


# ---- MeResponse ------------------------------------------------------------
def test_me_response_from_user_principal_sorts_perms():
    me = MeResponse.from_principal(_user({"pool.read", "agent.read"}))
    assert me.kind == "user" and me.role_code == "user" and me.is_super is False
    assert me.permissions == ["agent.read", "pool.read"]


def test_me_response_super_has_empty_perms():
    me = MeResponse.from_principal(_user(set(), is_super=True, role="super_admin"))
    assert me.is_super is True and me.permissions == []


# ---- ForbiddenException → 403 ----------------------------------------------
def test_forbidden_exception_maps_to_403():
    from app.layer4_infrastructure.middleware.error_handlers import domain_exception_handler

    assert issubclass(ForbiddenException, DomainException)
    req = SimpleNamespace(url=SimpleNamespace(path="/x"))
    resp = _run(domain_exception_handler(req, ForbiddenException("nope")))
    assert resp.status_code == 403


# ---- boot smoke ------------------------------------------------------------
def test_app_registers_me_route():
    from bootstrap import create_app

    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/v1/agents-registry/me" in paths
