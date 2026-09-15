"""Request DTOs for the Section variable-KEY-SCHEMA REST API (SA-2471)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SchemaKeyDTO(BaseModel):
    key: str
    type: str = "text"                       # text | credential | list


class CreateEnvSectionSchemaRequest(BaseModel):
    section: str = ""
    keys: list[SchemaKeyDTO] = Field(default_factory=list)


class UpdateEnvSectionSchemaRequest(BaseModel):
    # Partial update — a None field is left unchanged. ``section`` renames the
    # section (SA-2450 W18); ``keys`` replaces the shared key schema.
    section: str | None = None
    keys: list[SchemaKeyDTO] | None = None


def key_dtos_to_dicts(keys: list[SchemaKeyDTO]) -> list[dict[str, Any]]:
    return [{"key": k.key, "type": k.type} for k in keys]
