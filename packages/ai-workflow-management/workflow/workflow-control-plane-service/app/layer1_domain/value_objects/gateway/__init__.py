"""Value objects for the Gateway App + Function registry.

All gateway domain rules live here (and in the GatewayApp / GatewayFunction
entities). Use cases never re-implement validation — they call these and map
the raised ``ValidationError`` to an ``ErrorCode``.
"""
from __future__ import annotations

from app.layer1_domain.value_objects.gateway.app_kind import AppKind
from app.layer1_domain.value_objects.gateway.auth_config_spec import validate_auth_config
from app.layer1_domain.value_objects.gateway.auth_header import (
    AuthHeader,
    materialize_auth_header,
)
from app.layer1_domain.value_objects.gateway.function_param import FunctionParam
from app.layer1_domain.value_objects.gateway.function_target import (
    validate_function_target,
)
from app.layer1_domain.value_objects.gateway.host_allowlist import HostAllowlist
from app.layer1_domain.value_objects.gateway.http_method import HttpMethod
from app.layer1_domain.value_objects.gateway.openapi_import import (
    FunctionSignature,
    parse_openapi,
)
from app.layer1_domain.value_objects.gateway.output_field import OutputField
from app.layer1_domain.value_objects.gateway.param_location import ParamLocation
from app.layer1_domain.value_objects.gateway.path_template import PathTemplate
from app.layer1_domain.value_objects.gateway.resource_name import (
    MAX_NAME_LENGTH,
    name_error,
)

__all__ = [
    "AppKind",
    "HttpMethod",
    "ParamLocation",
    "FunctionParam",
    "OutputField",
    "PathTemplate",
    "HostAllowlist",
    "name_error",
    "MAX_NAME_LENGTH",
    "validate_auth_config",
    "validate_function_target",
    "AuthHeader",
    "materialize_auth_header",
    "parse_openapi",
    "FunctionSignature",
]
