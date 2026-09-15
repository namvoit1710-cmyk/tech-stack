"""Request DTOs for the gateway app + function registry admin API.

Response shapes come from the use-case result dataclasses (rendered by
``result_or_error``), so they are not duplicated here.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateGatewayAppRequest(BaseModel):
    name: str
    kind: str = "internal"            # internal | external
    base_url: str = ""               # internal apps
    allowed_hosts: list[str] = Field(default_factory=list)  # external apps
    auth_type: str = "none"
    auth_config: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    spec_url: str = ""               # OpenAPI spec URL to import + sync functions from


class UpdateGatewayAppRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    allowed_hosts: list[str] | None = None
    auth_type: str | None = None
    auth_config: dict[str, Any] | None = None
    description: str | None = None
    spec_url: str | None = None      # None = leave alone; "" = clear


class TestGatewayAppRequest(BaseModel):
    timeout_s: float = 5.0


class CreateGatewayFunctionRequest(BaseModel):
    name: str
    method: str = "GET"
    path_template: str
    input_params: list[dict[str, Any]] = Field(default_factory=list)
    output_fields: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""
    timeout_ms: int = 30_000
    icon: str = ""
    color: str = ""
    tags: list[str] = Field(default_factory=list)


class UpdateGatewayFunctionRequest(BaseModel):
    name: str | None = None
    method: str | None = None
    path_template: str | None = None
    # None = leave alone; [] = clear to empty (the controller maps None → _UNSET).
    input_params: list[dict[str, Any]] | None = None
    output_fields: list[dict[str, Any]] | None = None
    description: str | None = None
    timeout_ms: int | None = None
    icon: str | None = None
    color: str | None = None
    tags: list[str] | None = None


class ResolveGatewayFunctionByNameRequest(BaseModel):
    """Worker-facing selector for the name-addressed `_resolve`.

    Function ids are minted per-environment, so a workflow pinning one cannot be
    promoted dev → qa → prod unchanged; the (app, function) pair is stable.
    """
    app: str
    function: str
