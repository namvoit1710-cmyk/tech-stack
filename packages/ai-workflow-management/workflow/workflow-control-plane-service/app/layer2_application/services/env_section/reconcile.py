"""Reconcile every Environment in a Section to its shared key schema (SA-2471).

The section schema is the source of truth for the key *set* and each key's
*type*; environments own only the per-key VALUES. When a schema is created or
updated, every active environment in ``(tenant, section)`` is rewritten so its
variable keys == the schema keys:

* a KEPT key -> keep the environment's existing value;
* a NEW key  -> add it with an empty value;
* a REMOVED key -> drop it;
* if a kept key's TYPE changed vs the environment's stored variable, the value
  is RESET (an empty text value can't stand in for a list/credential, and vice
  versa) — the schema type always wins.

Each changed environment is saved with a version bump (CAS against its current
version). This module has no I/O of its own beyond the two repos it is handed,
so it is trivially unit-testable.
"""
from __future__ import annotations

from app.layer1_domain.entities.env_section_schema import EnvSectionSchema
from app.layer1_domain.value_objects.env_variable import EnvVariable, VariableType
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
    ListEnvironmentsFilter,
)


def _default_value_for(type_: VariableType) -> tuple[str, tuple[str, ...]]:
    """The empty value/values pair for a freshly-added or reset key of ``type_``.

    A list variable must carry at least one value to satisfy EnvVariable.validate,
    so a reset/new list key seeds a single empty entry; text/credential use an
    empty string value. (A credential validate() also wants a non-empty ref, but
    reconcile builds variables directly — bypassing make()/validate — precisely
    so an operator can fill the matrix cell afterwards.)
    """
    if type_ is VariableType.LIST:
        return "", ("",)
    return "", ()


def reconcile_environment_variables(
    schema: EnvSectionSchema, env_variables: list[EnvVariable]
) -> list[EnvVariable]:
    """Pure function: return the new variable list for one environment.

    Kept keys keep their value (unless the type changed); new keys get an empty
    value; removed keys are dropped; every variable's type is set from the schema.
    """
    existing: dict[str, EnvVariable] = {v.key: v for v in env_variables}
    out: list[EnvVariable] = []
    for sk in schema.keys:
        prev = existing.get(sk.key)
        if prev is None:
            value, values = _default_value_for(sk.type)
            out.append(EnvVariable(key=sk.key, type=sk.type, value=value, values=values))
            continue
        if prev.type is not sk.type:
            # Type changed → the old value can't be trusted; reset per new type.
            value, values = _default_value_for(sk.type)
            out.append(EnvVariable(key=sk.key, type=sk.type, value=value, values=values))
            continue
        # Kept, same type → preserve the value, re-stamp the (unchanged) type.
        out.append(
            EnvVariable(key=sk.key, type=sk.type, value=prev.value, values=prev.values)
        )
    return out


def reconcile_section(
    schema: EnvSectionSchema, environment_repo: IEnvironmentRepo
) -> int:
    """Reconcile all ACTIVE environments in the schema's section. Returns the
    number of environments saved (all of them; each save bumps its version)."""
    page = environment_repo.list_paged(
        ListEnvironmentsFilter(
            tenant_id=schema.tenant_id,
            section=schema.section,
            include_inactive=False,
            skip=0,
            top=100000,
        )
    )
    saved = 0
    for env in page.items:
        new_vars = reconcile_environment_variables(schema, env.variables)
        expected = env.version
        # Mutate through the aggregate so it touches version/audit metadata.
        env.variables = new_vars
        env.touch(updated_by=schema.updated_by or schema.created_by or "system")
        environment_repo.save(env, expected_version=expected)
        saved += 1
    return saved
