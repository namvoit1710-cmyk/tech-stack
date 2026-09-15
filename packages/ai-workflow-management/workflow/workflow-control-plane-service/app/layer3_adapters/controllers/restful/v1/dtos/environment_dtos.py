"""Request/response DTOs for the Environment (config variables) REST API (SA-2365)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.layer1_domain.entities.environment import Environment


class VariableDTO(BaseModel):
    key: str
    type: str = "text"                       # text | base_url | credential | list
    value: str = ""                          # text/base_url/credential(ref)
    values: list[str] = Field(default_factory=list)  # list type only


class CreateEnvironmentRequest(BaseModel):
    section: str = ""
    name: str
    variables: list[VariableDTO] = Field(default_factory=list)


class UpdateEnvironmentRequest(BaseModel):
    # Partial update — a None field is left unchanged.
    section: str | None = None
    name: str | None = None
    variables: list[VariableDTO] | None = None


def variable_dtos_to_dicts(variables: list[VariableDTO]) -> list[dict[str, Any]]:
    return [
        {"key": v.key, "type": v.type, "value": v.value, "values": v.values}
        for v in variables
    ]


def env_to_dict(env: Environment) -> dict[str, Any]:
    """Projection for GET/LIST responses. Config variable values are non-secret
    (a credential-type variable only carries a credential_ref id, never a secret),
    so they are safe to return; the actual secret stays in the vault."""
    return {
        "id": env.id,
        "tenant_id": env.tenant_id,
        "section": env.section,
        "name": env.name,
        "is_active": env.is_active,
        "version": env.version,
        "created_at": env.created_at,
        "updated_at": env.updated_at,
        "variables": [
            {
                "key": v.key,
                "type": v.type.value,
                "value": v.value,
                "values": list(v.values),
                "is_secret": v.is_secret,
            }
            for v in env.variables
        ],
    }
