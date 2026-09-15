"""Validate + inherit a Section's key schema on an Environment save (SA-2471, §7).

When a section schema exists for an environment's ``(tenant, section)``, the
environment's variables are GOVERNED by it:

* every variable key the env carries must be one of the schema keys — an unknown
  key is rejected (422 ENVIRONMENT_VARIABLES_INVALID, details ``{unknown_keys}``);
* any schema key the env does NOT provide is INITIALIZED to an empty value, so a
  new environment inherits the full schema;
* each variable's TYPE is set from the schema (the schema is the source of truth
  for type).

If NO schema exists for the section, this is a no-op — legacy SA-2365 behavior is
preserved (backward compatible; existing tests unaffected).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.layer1_domain.value_objects.env_variable import EnvVariable, VariableType
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    IEnvSectionSchemaRepo,
)


@dataclass
class SchemaGuardResult:
    ok: bool
    variables: list[EnvVariable]          # the reconciled variable list (when ok)
    unknown_keys: list[str]               # populated when not ok


def _default_value_for(type_: VariableType) -> tuple[str, tuple[str, ...]]:
    if type_ is VariableType.LIST:
        return "", ("",)
    return "", ()


def apply_section_schema(
    *,
    tenant_id: str,
    section: str,
    variables: list[EnvVariable],
    repo: IEnvSectionSchemaRepo | None,
) -> SchemaGuardResult:
    """Return the schema-conformed variable list, or the set of unknown keys.

    ``repo is None`` (feature not wired) or no active schema -> pass the variables
    through unchanged (legacy behavior)."""
    if repo is None:
        return SchemaGuardResult(ok=True, variables=list(variables), unknown_keys=[])

    schema = repo.get_by_natural_key(tenant_id=tenant_id, section=section)
    if schema is None or not schema.is_active:
        return SchemaGuardResult(ok=True, variables=list(variables), unknown_keys=[])

    allowed = {sk.key: sk.type for sk in schema.keys}
    provided = {v.key: v for v in variables}

    unknown = [k for k in provided if k not in allowed]
    if unknown:
        return SchemaGuardResult(ok=False, variables=[], unknown_keys=sorted(unknown))

    return _reconcile(schema.keys, provided)


def _reconcile(schema_keys, provided: dict) -> SchemaGuardResult:
    out: list[EnvVariable] = []
    for sk in schema_keys:
        prev = provided.get(sk.key)
        if prev is None:
            value, values = _default_value_for(sk.type)
            out.append(EnvVariable(key=sk.key, type=sk.type, value=value, values=values))
            continue
        if prev.type is not sk.type:
            # Env supplied a value but the schema type differs → re-type; keep the
            # value only when it still fits (text/credential share a string slot).
            if sk.type is VariableType.LIST:
                out.append(EnvVariable(key=sk.key, type=sk.type, value="", values=("",)))
            else:
                out.append(
                    EnvVariable(key=sk.key, type=sk.type, value=prev.value, values=())
                )
            continue
        out.append(
            EnvVariable(key=sk.key, type=sk.type, value=prev.value, values=prev.values)
        )
    return SchemaGuardResult(ok=True, variables=out, unknown_keys=[])


def apply_section_schemas(
    *,
    tenant_id: str,
    sections: list[str],
    variables: list[EnvVariable],
    repo: IEnvSectionSchemaRepo | None,
) -> SchemaGuardResult:
    """M:N variant (SA-2605): validate/reconcile the env's variables against the
    UNION of the schemas of EVERY section the environment belongs to.

    An environment is a member of many sections; its variable keys are the union of
    those sections' shared-key schemas. A key valid in ANY of the env's sections is
    allowed. Same-key-same-type across sections is guaranteed by the SA-2613
    membership guard, so the first schema wins on a key collision. Pass-through when
    no section has an active schema (legacy behavior)."""
    if repo is None:
        return SchemaGuardResult(ok=True, variables=list(variables), unknown_keys=[])

    union_keys = []              # preserves first-seen order
    seen: set[str] = set()
    any_schema = False
    for section in sections:
        if not (section or "").strip():
            continue
        schema = repo.get_by_natural_key(tenant_id=tenant_id, section=section)
        if schema is None or not schema.is_active:
            continue
        any_schema = True
        for sk in schema.keys:
            if sk.key not in seen:
                seen.add(sk.key)
                union_keys.append(sk)
    if not any_schema:
        return SchemaGuardResult(ok=True, variables=list(variables), unknown_keys=[])

    allowed = {sk.key for sk in union_keys}
    provided = {v.key: v for v in variables}
    unknown = [k for k in provided if k not in allowed]
    if unknown:
        return SchemaGuardResult(ok=False, variables=[], unknown_keys=sorted(unknown))
    return _reconcile(union_keys, provided)
