"""P2 unit tests: domain auth-header materialization + the resolve / test use cases."""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
from app.layer1_domain.value_objects.gateway import (
    AppKind,
    AuthHeader,
    FunctionParam,
    HostAllowlist,
    HttpMethod,
    ParamLocation,
    PathTemplate,
    materialize_auth_header,
)
from app.layer2_application.features.resolve_gateway_function.use_cases.resolve_gateway_function_use_case import (
    ResolveGatewayFunctionCommand,
    ResolveGatewayFunctionUseCase,
)
from app.layer2_application.features.test_gateway_app.use_cases.test_gateway_app_use_case import (
    TestGatewayAppCommand,
    TestGatewayAppUseCase,
)
from app.layer2_application.interfaces.http_probe_interface import HttpProbeResult
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


class _FakeCipher:
    current_key_version = 1

    def __init__(self, plaintext: dict): self._p = plaintext
    def encrypt(self, p): return EncryptedCredential("ct", 1)
    def decrypt(self, enc): return self._p


def _seed(apps, fns, *, kind=AppKind.INTERNAL, auth=AuthType.BEARER):
    app = GatewayApp.create(
        name="gov", kind=kind,
        base_url="https://gov.internal" if kind is AppKind.INTERNAL else "",
        allowed_hosts=None if kind is AppKind.INTERNAL else HostAllowlist(("api.partner.com",)),
        auth_type=auth,
        auth_config_encrypted=EncryptedCredential("ct", 1) if auth is not AuthType.NONE else None,
        created_by="me",
    )
    apps.save(app)
    fn = GatewayFunction.create(
        app_id=app.id, name="get_order", method=HttpMethod.GET,
        path_template=PathTemplate("/orders/{order_id}"),
        input_params=[FunctionParam(key="order_id", location=ParamLocation.PATH, required=True)],
        created_by="me",
    )
    fns.save(fn)
    return app, fn


def test_materialize_auth_header():
    assert materialize_auth_header(AuthType.NONE, {}) is None
    assert materialize_auth_header(AuthType.BEARER, {"token": "t"}) == AuthHeader("Authorization", "Bearer t")
    assert materialize_auth_header(AuthType.API_KEY_HEADER, {"header": "X-Key", "value": "v"}) == AuthHeader("X-Key", "v")
    with pytest.raises(NotImplementedError):
        materialize_auth_header(AuthType.OAUTH_CC, {})


@pytest.mark.asyncio
async def test_resolve_internal_bearer_materializes_header():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    _, fn = _seed(apps, fns, auth=AuthType.BEARER)
    uc = ResolveGatewayFunctionUseCase(_Log(), apps, fns, _FakeCipher({"token": "sek"}))
    r = await uc.execute(ResolveGatewayFunctionCommand(function_id=fn.id))
    assert not r.error_code
    assert r.kind == "internal" and r.base_url == "https://gov.internal" and r.method == "GET"
    assert r.path_template == "/orders/{order_id}"
    assert r.params and r.params[0]["location"] == "path"
    assert r.auth_header_name == "Authorization" and r.auth_header_value == "Bearer sek"


@pytest.mark.asyncio
async def test_resolve_external_has_allowed_hosts_and_no_auth():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    _, fn = _seed(apps, fns, kind=AppKind.EXTERNAL, auth=AuthType.NONE)
    uc = ResolveGatewayFunctionUseCase(_Log(), apps, fns, _FakeCipher({}))
    r = await uc.execute(ResolveGatewayFunctionCommand(function_id=fn.id))
    assert r.kind == "external" and r.allowed_hosts == ["api.partner.com"]
    assert r.auth_header_name == "" and r.auth_header_value == ""


@pytest.mark.asyncio
async def test_resolve_function_not_found():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    uc = ResolveGatewayFunctionUseCase(_Log(), apps, fns, _FakeCipher({}))
    r = await uc.execute(ResolveGatewayFunctionCommand(function_id="nope"))
    assert r.error_code == "GATEWAY_FUNCTION_NOT_FOUND"


@pytest.mark.asyncio
async def test_test_app_internal_probes_base_url_with_auth():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    _seed(apps, fns, auth=AuthType.BEARER)
    captured: dict = {}

    async def fake_probe(url, headers, timeout_s):
        captured["url"] = url
        captured["headers"] = headers
        return HttpProbeResult(reachable=True, status_code=200, latency_ms=1.0)

    uc = TestGatewayAppUseCase(_Log(), apps, _FakeCipher({"token": "sek"}), fake_probe)
    r = await uc.execute(TestGatewayAppCommand(name="gov"))
    assert r.reachable and r.status_code == 200
    assert captured["url"] == "https://gov.internal"
    assert captured["headers"]["Authorization"] == "Bearer sek"
