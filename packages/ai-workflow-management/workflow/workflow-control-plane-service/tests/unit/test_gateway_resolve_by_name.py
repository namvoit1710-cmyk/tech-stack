"""Resolve a gateway function by (app name, function name) instead of by id.

The id is minted per-environment, so a workflow pinning one cannot be promoted
dev → qa → prod unchanged. These tests pin the name selector, the id selector it
must not disturb, and the ways the pair can be wrong.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
from app.layer1_domain.value_objects.gateway import (
    AppKind,
    FunctionParam,
    HttpMethod,
    ParamLocation,
    PathTemplate,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.resolve_gateway_function.use_cases.resolve_gateway_function_use_case import (
    ResolveGatewayFunctionCommand,
    ResolveGatewayFunctionUseCase,
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


class _FakeCipher:
    current_key_version = 1

    def encrypt(self, p): return EncryptedCredential("ct", 1)
    def decrypt(self, enc): return {"token": "t"}


def _seed(*, fn_name="get_order", app_name="gov"):
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    app = GatewayApp.create(
        name=app_name,
        kind=AppKind.INTERNAL,
        base_url="https://gov.internal",
        allowed_hosts=None,
        auth_type=AuthType.BEARER,
        auth_config_encrypted=EncryptedCredential("ct", 1),
        created_by="me",
    )
    apps.save(app)
    fn = GatewayFunction.create(
        app_id=app.id,
        name=fn_name,
        method=HttpMethod.GET,
        path_template=PathTemplate("/orders/{order_id}"),
        input_params=[FunctionParam(key="order_id", location=ParamLocation.PATH, required=True)],
        created_by="me",
    )
    fns.save(fn)
    uc = ResolveGatewayFunctionUseCase(_Log(), apps, fns, _FakeCipher())
    return uc, apps, fns, app, fn


@pytest.mark.asyncio
async def test_resolve_by_name_returns_the_same_spec_as_by_id():
    """The whole point: the two selectors are interchangeable addresses."""
    uc, _, _, _, fn = _seed()

    by_name = await uc.execute(
        ResolveGatewayFunctionCommand(app_name="gov", function_name="get_order")
    )
    by_id = await uc.execute(ResolveGatewayFunctionCommand(function_id=fn.id))

    assert by_name.error == ""
    assert by_name.path_template == "/orders/{order_id}"
    assert by_name.base_url == "https://gov.internal"
    assert by_name.method == "GET"
    # The credential is materialized on the name path too — not just the id path.
    assert (by_name.auth_header_name, by_name.auth_header_value) == ("Authorization", "Bearer t")
    assert by_name == by_id


@pytest.mark.asyncio
async def test_resolve_by_name_trims_whitespace():
    uc, *_ = _seed()
    result = await uc.execute(
        ResolveGatewayFunctionCommand(app_name="  gov  ", function_name="  get_order  ")
    )
    assert result.error == ""
    assert result.path_template == "/orders/{order_id}"


@pytest.mark.asyncio
async def test_unknown_app_name_is_app_not_found():
    uc, *_ = _seed()
    result = await uc.execute(
        ResolveGatewayFunctionCommand(app_name="nope", function_name="get_order")
    )
    assert result.error_code == ErrorCode.GATEWAY_APP_NOT_FOUND.code
    assert result.error_details == {"app_name": "nope"}


@pytest.mark.asyncio
async def test_unknown_function_name_is_function_not_found():
    uc, *_ = _seed()
    result = await uc.execute(
        ResolveGatewayFunctionCommand(app_name="gov", function_name="nope")
    )
    assert result.error_code == ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code
    assert result.error_details == {"app_name": "gov", "function_name": "nope"}


@pytest.mark.asyncio
async def test_function_name_is_scoped_to_its_app():
    """Same function name in another app must not leak across the app boundary."""
    uc, apps, fns, _, _ = _seed()
    other = GatewayApp.create(
        name="other", kind=AppKind.INTERNAL, base_url="https://other.internal",
        allowed_hosts=None, auth_type=AuthType.NONE, auth_config_encrypted=None,
        created_by="me",
    )
    apps.save(other)
    # `other` has NO function named get_order.
    result = await uc.execute(
        ResolveGatewayFunctionCommand(app_name="other", function_name="get_order")
    )
    assert result.error_code == ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code


@pytest.mark.asyncio
async def test_soft_deleted_function_is_not_resolvable_by_name():
    uc, _, fns, _, fn = _seed()
    fns.soft_delete(fn.id)
    result = await uc.execute(
        ResolveGatewayFunctionCommand(app_name="gov", function_name="get_order")
    )
    assert result.error_code == ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code


@pytest.mark.asyncio
async def test_soft_deleted_app_is_not_resolvable_by_name():
    uc, apps, _, app, _ = _seed()
    apps.soft_delete(app.id)
    result = await uc.execute(
        ResolveGatewayFunctionCommand(app_name="gov", function_name="get_order")
    )
    assert result.error_code == ErrorCode.GATEWAY_APP_NOT_FOUND.code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        ResolveGatewayFunctionCommand(),                          # nothing at all
        ResolveGatewayFunctionCommand(app_name="gov"),            # app without function
        ResolveGatewayFunctionCommand(function_name="get_order"), # function without app
        ResolveGatewayFunctionCommand(app_name="  ", function_name="  "),  # blank
    ],
)
async def test_incomplete_selector_is_a_validation_error_not_a_404(command):
    """A half-supplied name pair is the caller's bug — say so, don't say 'not found'."""
    uc, *_ = _seed()
    result = await uc.execute(command)
    assert result.error_code == ErrorCode.VALIDATION_ERROR.code


@pytest.mark.asyncio
async def test_resolve_by_id_still_works_unchanged():
    """Regression guard: the id path predates this and must keep its exact behaviour."""
    uc, _, _, _, fn = _seed()
    result = await uc.execute(ResolveGatewayFunctionCommand(function_id=fn.id))
    assert result.error == ""
    assert result.path_template == "/orders/{order_id}"

    missing = await uc.execute(ResolveGatewayFunctionCommand(function_id="does-not-exist"))
    assert missing.error_code == ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code
    assert missing.error_details == {"function_id": "does-not-exist"}
