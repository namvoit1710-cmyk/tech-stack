"""SA-2365 — Environment aggregate + in-memory repo (model + store).

Covers: create/validate, variable types (text/base_url/credential/list),
secret flag, CAS on update, natural-key (tenant, section, name) uniqueness,
tenant-scoped listing.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.env_variable import EnvVariable, VariableType
from app.layer2_application.repositories.environment_repo_interface import (
    CASMismatch,
    ListEnvironmentsFilter,
)
from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
    MemoryEnvironmentRepo,
)


def _env(name="qas", section="SimpleMDG", tenant="t1"):
    return Environment.create(
        tenant_id=tenant,
        section=section,
        name=name,
        created_by="rowan",
        variables=[
            EnvVariable.make(key="base_url", type="text", value="https://srv-qas"),
            EnvVariable.make(key="auth", type="credential", value="cred_s4qas"),
            EnvVariable.make(key="CLAUDE_TOKENS", type="list", values=["a", "b", "c"]),
        ],
    )


def test_create_and_variable_types():
    env = _env()
    assert env.name == "qas"
    assert env.version == 1
    assert env.get("base_url").type is VariableType.TEXT
    assert env.get("auth").type is VariableType.CREDENTIAL
    assert env.get("auth").is_secret is True
    assert env.get("base_url").is_secret is False
    assert env.get("CLAUDE_TOKENS").values == ("a", "b", "c")


def test_credential_variable_allows_empty_ref():
    # SA-2471: an empty credential value now means "not configured for this
    # environment yet" and is allowed. A section's credential key is inherited
    # UNSET by every environment (K3/K8) and filled in per environment later;
    # runtime $env resolution still errors clearly if it stays empty. (SA-2365
    # originally rejected an empty credential value here.)
    v = EnvVariable.make(key="auth", type="credential", value="")
    assert v.value == ""
    assert v.validate() == []


def test_list_variable_requires_values():
    with pytest.raises(ValidationError):
        EnvVariable.make(key="pool", type="list", values=[])


def test_invalid_key_rejected():
    with pytest.raises(ValidationError):
        EnvVariable.make(key="bad key!", type="text", value="x")


def test_duplicate_keys_rejected():
    with pytest.raises(ValidationError):
        Environment.create(
            tenant_id="t1", section="S", name="qas", created_by="r",
            variables=[
                EnvVariable.make(key="dup", value="1"),
                EnvVariable.make(key="dup", value="2"),
            ],
        )


def test_save_get_and_cas_update():
    repo = MemoryEnvironmentRepo()
    env = _env()
    repo.save(env)
    got = repo.get_by_id(env.id)
    assert got is not None and got.version == 1

    got.rename("qas-renamed", updated_by="rowan")
    repo.save(got, expected_version=1)
    assert repo.get_by_id(env.id).name == "qas-renamed"
    assert repo.get_by_id(env.id).version == 2


def test_cas_mismatch_raises():
    repo = MemoryEnvironmentRepo()
    env = _env()
    repo.save(env)
    with pytest.raises(CASMismatch):
        repo.save(env, expected_version=999)


def test_natural_key_uniqueness():
    repo = MemoryEnvironmentRepo()
    repo.save(_env(name="qas"))
    with pytest.raises(CASMismatch):
        repo.save(_env(name="qas"))  # same (tenant, section, name)


def test_get_by_natural_key():
    repo = MemoryEnvironmentRepo()
    env = _env(name="prod")
    repo.save(env)
    found = repo.get_by_natural_key(tenant_id="t1", section="SimpleMDG", name="prod")
    assert found is not None and found.id == env.id


def test_list_is_tenant_scoped():
    repo = MemoryEnvironmentRepo()
    repo.save(_env(name="qas", tenant="t1"))
    repo.save(_env(name="prod", tenant="t1"))
    repo.save(_env(name="qas", tenant="t2"))
    page = repo.list_paged(ListEnvironmentsFilter(tenant_id="t1"))
    assert page.total == 2
    assert all(e.tenant_id == "t1" for e in page.items)


def test_upsert_and_remove_variable():
    env = _env()
    env.upsert_variable(EnvVariable.make(key="base_url", type="text", value="https://new"), updated_by="r")
    assert env.get("base_url").value == "https://new"
    env.remove_variable("auth", updated_by="r")
    assert env.get("auth") is None


def test_soft_delete_hides_from_default_list():
    repo = MemoryEnvironmentRepo()
    env = _env()
    repo.save(env)
    repo.soft_delete(env.id)
    page = repo.list_paged(ListEnvironmentsFilter(tenant_id="t1"))
    assert page.total == 0
    page_all = repo.list_paged(ListEnvironmentsFilter(tenant_id="t1", include_inactive=True))
    assert page_all.total == 1
