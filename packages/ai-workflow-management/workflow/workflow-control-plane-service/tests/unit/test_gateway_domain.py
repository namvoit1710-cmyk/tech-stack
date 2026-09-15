"""Domain-layer tests for the Gateway App + Function registry.

The bulk of gateway coverage lives here because (per clean architecture) all
rules live in the domain: value objects validate on construction, entity
mutators validate + bump version, and ``validate()`` owns the invariants.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
from app.layer1_domain.value_objects.gateway import (
    AppKind,
    FunctionParam,
    HostAllowlist,
    HttpMethod,
    OutputField,
    ParamLocation,
    PathTemplate,
    validate_auth_config,
)
from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.entities.gateway_function import GatewayFunction


# ── Value objects ────────────────────────────────────────────────────

def test_http_method_coerce_case_insensitive_and_rejects_unknown():
    assert HttpMethod.coerce("get") is HttpMethod.GET
    assert HttpMethod.coerce(" Post ") is HttpMethod.POST
    with pytest.raises(ValueError):
        HttpMethod.coerce("FETCH")


def test_path_template_placeholder_extraction_and_malformed_rejected():
    assert PathTemplate("/orders/{order_id}/items/{item_id}").placeholders() == {
        "order_id", "item_id",
    }
    assert PathTemplate("/health").placeholders() == frozenset()
    with pytest.raises(ValidationError):
        PathTemplate("")
    with pytest.raises(ValidationError):
        PathTemplate("/orders/{bad-name}")  # hyphen not allowed in token


def test_function_param_validates_type_and_location():
    p = FunctionParam(key="q", type="string", location=ParamLocation.QUERY)
    assert p.to_dict()["location"] == "query"
    assert FunctionParam.from_dict(p.to_dict()) == p
    with pytest.raises(ValidationError):
        FunctionParam(key="", type="string")
    with pytest.raises(ValidationError):
        FunctionParam(key="q", type="weird")


def test_host_allowlist_rejects_bad_hosts():
    HostAllowlist(("api.partner.com", "*.crm.example.com"))  # ok
    with pytest.raises(ValidationError):
        HostAllowlist(("https://api.partner.com",))  # scheme not allowed
    with pytest.raises(ValidationError):
        HostAllowlist(("",))


def test_validate_auth_config():
    validate_auth_config(AuthType.BEARER, {"token": "x"})
    validate_auth_config(AuthType.NONE, {})
    with pytest.raises(ValidationError):
        validate_auth_config(AuthType.BEARER, {})
    with pytest.raises(ValidationError):
        validate_auth_config(AuthType.API_KEY_HEADER, {"header": "X"})  # missing value


# ── GatewayApp entity ────────────────────────────────────────────────

def _cred() -> EncryptedCredential:
    return EncryptedCredential(ciphertext_b64="ct", key_version=1)


def test_app_create_internal_requires_base_url():
    with pytest.raises(ValidationError):
        GatewayApp.create(name="x", kind=AppKind.INTERNAL, created_by="me")
    app = GatewayApp.create(
        name="gov", kind=AppKind.INTERNAL, base_url="https://gov", created_by="me",
    )
    assert app.version == 1 and app.created_by == "me" and app.updated_by == "me"


def test_app_create_external_requires_allowed_hosts():
    with pytest.raises(ValidationError):
        GatewayApp.create(name="x", kind=AppKind.EXTERNAL, created_by="me")
    app = GatewayApp.create(
        name="p", kind=AppKind.EXTERNAL,
        allowed_hosts=HostAllowlist(("api.partner.com",)), created_by="me",
    )
    assert app.allowed_hosts.to_list() == ["api.partner.com"]


def test_app_mutators_validate_and_touch():
    app = GatewayApp.create(
        name="gov", kind=AppKind.INTERNAL, base_url="https://gov", created_by="me",
    )
    app.change_base_url("https://gov2", updated_by="u2")
    assert app.base_url == "https://gov2" and app.version == 2 and app.updated_by == "u2"
    with pytest.raises(ValidationError):
        app.change_base_url("", updated_by="u2")
    # base_url is meaningless on an external app
    ext = GatewayApp.create(
        name="p", kind=AppKind.EXTERNAL,
        allowed_hosts=HostAllowlist(("a.com",)), created_by="me",
    )
    with pytest.raises(ValidationError):
        ext.change_base_url("https://x", updated_by="me")


def test_app_set_auth_requires_credential_for_non_none():
    app = GatewayApp.create(
        name="gov", kind=AppKind.INTERNAL, base_url="https://gov", created_by="me",
    )
    with pytest.raises(ValidationError):
        app.set_auth(AuthType.BEARER, None, updated_by="me")
    app.set_auth(AuthType.BEARER, _cred(), updated_by="me")
    assert app.credential_summary()["credential_present"] is True
    app.clear_auth(updated_by="me")
    assert app.auth_type is AuthType.NONE
    assert app.credential_summary()["credential_present"] is False


# ── GatewayFunction entity ───────────────────────────────────────────

def test_function_create_enforces_path_param_consistency():
    fn = GatewayFunction.create(
        app_id="a1", name="get_order", method=HttpMethod.GET,
        path_template=PathTemplate("/orders/{order_id}"),
        input_params=[FunctionParam(key="order_id", location=ParamLocation.PATH, required=True)],
        created_by="me",
    )
    assert fn.version == 1
    # placeholder with no matching path param
    with pytest.raises(ValidationError):
        GatewayFunction.create(
            app_id="a1", name="bad", method=HttpMethod.GET,
            path_template=PathTemplate("/orders/{order_id}"), created_by="me",
        )
    # path param not referenced by the template
    with pytest.raises(ValidationError):
        GatewayFunction.create(
            app_id="a1", name="bad2", method=HttpMethod.GET,
            path_template=PathTemplate("/orders"),
            input_params=[FunctionParam(key="x", location=ParamLocation.PATH)],
            created_by="me",
        )


def test_function_set_signature_atomic_and_touch():
    fn = GatewayFunction.create(
        app_id="a1", name="f", method=HttpMethod.GET,
        path_template=PathTemplate("/o/{id}"),
        input_params=[FunctionParam(key="id", location=ParamLocation.PATH)],
        created_by="me",
    )
    fn.set_signature(
        updated_by="u2",
        method=HttpMethod.POST,
        path_template=PathTemplate("/orders/{oid}"),
        input_params=[FunctionParam(key="oid", location=ParamLocation.PATH)],
    )
    assert fn.method is HttpMethod.POST
    assert fn.path_template.value == "/orders/{oid}"
    assert fn.version == 2
    # an inconsistent atomic change is rejected wholesale
    with pytest.raises(ValidationError):
        fn.set_signature(updated_by="u2", path_template=PathTemplate("/orders/{nope}"))
    assert fn.path_template.value == "/orders/{oid}"  # unchanged


def test_function_change_timeout_and_presentation():
    fn = GatewayFunction.create(
        app_id="a1", name="f", method=HttpMethod.GET,
        path_template=PathTemplate("/health"), created_by="me",
    )
    with pytest.raises(ValidationError):
        fn.change_timeout(0, updated_by="me")
    fn.change_timeout(5000, updated_by="me")
    assert fn.timeout_ms == 5000
    fn.set_presentation(updated_by="me", icon="Bolt", tags=["a", "b"])
    assert fn.icon == "Bolt" and fn.tags == ("a", "b")
