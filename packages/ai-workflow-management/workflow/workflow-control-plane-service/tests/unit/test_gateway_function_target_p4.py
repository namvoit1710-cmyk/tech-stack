"""P4 unit tests: external-target domain rule (HostAllowlist.matches +
validate_function_target) and the create/update function use cases enforcing it."""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import (
    AppKind,
    HostAllowlist,
    PathTemplate,
    validate_function_target,
)
from app.layer2_application.features.create_gateway_function.use_cases.create_gateway_function_use_case import (
    CreateGatewayFunctionCommand,
    CreateGatewayFunctionUseCase,
)
from app.layer2_application.features.update_gateway_function.use_cases.update_gateway_function_use_case import (
    UpdateGatewayFunctionCommand,
    UpdateGatewayFunctionUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_gateway_app_repo import (
    MemoryGatewayAppRepo,
)
from app.layer4_frameworks.persistence.memory.memory_gateway_function_repo import (
    MemoryGatewayFunctionRepo,
)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


# ── HostAllowlist.matches ─────────────────────────────────────────────

@pytest.mark.parametrize("host,expected", [
    ("api.partner.com", True),
    ("API.PARTNER.COM", True),     # case-insensitive
    ("x.svc.partner.com", True),   # one-level wildcard
    ("a.b.svc.partner.com", False),  # multi-level not allowed
    ("svc.partner.com", False),    # apex not matched by wildcard
    ("evil.com", False),
    ("", False),
])
def test_host_allowlist_matches(host, expected):
    al = HostAllowlist.from_list(["api.partner.com", "*.svc.partner.com"])
    assert al.matches(host) is expected


# ── validate_function_target ──────────────────────────────────────────

def _v(kind, hosts, path):
    validate_function_target(
        app_kind=kind,
        allowed_hosts=(HostAllowlist.from_list(hosts) if hosts is not None else None),
        path_template=PathTemplate(path),
    )


def test_internal_relative_ok():
    _v(AppKind.INTERNAL, None, "/orders/{order_id}")  # no raise


def test_internal_absolute_rejected():
    with pytest.raises(ValidationError):
        _v(AppKind.INTERNAL, None, "https://x.com/a")


def test_external_absolute_in_allowlist_ok():
    _v(AppKind.EXTERNAL, ["api.partner.com"], "https://api.partner.com/v1/orders/{order_id}")
    _v(AppKind.EXTERNAL, ["*.partner.com"], "https://api.partner.com/v1/{id}")  # wildcard + placeholder


def test_external_host_not_in_allowlist_rejected():
    with pytest.raises(ValidationError):
        _v(AppKind.EXTERNAL, ["api.partner.com"], "https://evil.com/v1")


def test_external_relative_rejected():
    with pytest.raises(ValidationError):
        _v(AppKind.EXTERNAL, ["api.partner.com"], "/v1/orders")


def test_external_empty_allowlist_rejected():
    with pytest.raises(ValidationError):
        _v(AppKind.EXTERNAL, [], "https://api.partner.com/v1")


# ── use cases enforce the rule ────────────────────────────────────────

def _seed_app(apps, *, kind, name="app1", hosts=None):
    app = GatewayApp.create(
        name=name, kind=kind,
        base_url="https://internal.svc" if kind is AppKind.INTERNAL else "",
        allowed_hosts=HostAllowlist.from_list(hosts) if hosts else None,
        auth_type=AuthType.NONE, created_by="me",
    )
    apps.save(app)
    return app


@pytest.mark.asyncio
async def test_create_external_function_in_allowlist_succeeds():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    _seed_app(apps, kind=AppKind.EXTERNAL, name="gh", hosts=["api.github.com"])
    uc = CreateGatewayFunctionUseCase(_Log(), apps, fns)
    r = await uc.execute(CreateGatewayFunctionCommand(
        app_name="gh", name="zen", method="GET",
        path_template="https://api.github.com/zen", created_by="me",
    ))
    assert not r.error_code and r.id


@pytest.mark.asyncio
async def test_create_external_function_bad_host_rejected():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    _seed_app(apps, kind=AppKind.EXTERNAL, name="gh", hosts=["api.github.com"])
    uc = CreateGatewayFunctionUseCase(_Log(), apps, fns)
    r = await uc.execute(CreateGatewayFunctionCommand(
        app_name="gh", name="evil", method="GET",
        path_template="https://evil.example.com/x", created_by="me",
    ))
    assert r.error_code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_internal_function_absolute_url_rejected():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    _seed_app(apps, kind=AppKind.INTERNAL, name="svc")
    uc = CreateGatewayFunctionUseCase(_Log(), apps, fns)
    r = await uc.execute(CreateGatewayFunctionCommand(
        app_name="svc", name="bad", method="GET",
        path_template="https://api.github.com/zen", created_by="me",
    ))
    assert r.error_code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_update_external_function_to_bad_host_rejected():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    _seed_app(apps, kind=AppKind.EXTERNAL, name="gh", hosts=["api.github.com"])
    uc = CreateGatewayFunctionUseCase(_Log(), apps, fns)
    created = await uc.execute(CreateGatewayFunctionCommand(
        app_name="gh", name="zen", method="GET",
        path_template="https://api.github.com/zen", created_by="me",
    ))
    upd = UpdateGatewayFunctionUseCase(_Log(), apps, fns)
    r = await upd.execute(UpdateGatewayFunctionCommand(
        app_name="gh", name="zen", expected_version=created.version, updated_by="me",
        new_path_template="https://evil.example.com/x",
    ))
    assert r.error_code == "VALIDATION_ERROR"
