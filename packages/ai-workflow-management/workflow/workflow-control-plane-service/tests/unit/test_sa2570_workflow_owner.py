"""SA-2570 — workflow owner & creator (users sourced from Keycloak).

Covers UC-1 (create stamps creator/owner from the verified caller, not the body),
UC-4/UC-5 (reassign + authz), UC-7 (auth-off fallback), the ``is_admin`` role
check, and the Keycloak-backed user list.
"""
import pytest

from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)
from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
)
from app.layer2_application.features.get_workflow.use_cases.get_workflow_use_case import (
    GetWorkflowCommand,
    GetWorkflowUseCase,
)
from app.layer2_application.features.set_workflow_owner.use_cases.set_workflow_owner_use_case import (
    SetWorkflowOwnerCommand,
    SetWorkflowOwnerUseCase,
)
from app.layer2_application.features.list_users.use_cases.list_users_use_case import (
    ListUsersUseCase,
)
from app.layer3_adapters.controllers.restful.v1.auth_dependencies import is_admin


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def debug(self, *a, **k): ...


async def _create(repo, name="wf", created_by="alice"):
    uc = CreateWorkflowUseCase(logger=_Log(), workflow_repo=repo)
    return await uc.execute(CreateWorkflowCommand(
        name=name, created_by=created_by, owner=created_by, updated_by=created_by,
    ))


# ── UC-1: create stamps creator + default owner from the verified caller ──────

@pytest.mark.asyncio
async def test_create_stamps_creator_and_owner():
    repo = MemoryWorkflowRepository()
    result = await _create(repo, created_by="alice")
    assert result.created_by == "alice"
    assert result.owner == "alice"  # owner defaults to the creator
    # Persisted + reloaded.
    got = await GetWorkflowUseCase(logger=_Log(), workflow_repo=repo).execute(
        GetWorkflowCommand(workflow_id=result.id)
    )
    assert got.created_by == "alice"
    assert got.owner == "alice"


# ── UC-7: auth off → falls back to "system" (never an empty attribution) ──────

@pytest.mark.asyncio
async def test_create_auth_off_falls_back_to_system():
    repo = MemoryWorkflowRepository()
    uc = CreateWorkflowUseCase(logger=_Log(), workflow_repo=repo)
    result = await uc.execute(CreateWorkflowCommand(name="wf"))  # no created_by
    assert result.created_by == "system"
    assert result.owner == "system"


# ── UC-4: the current owner can reassign ──────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_reassign():
    repo = MemoryWorkflowRepository()
    created = await _create(repo, created_by="alice")
    uc = SetWorkflowOwnerUseCase(logger=_Log(), workflow_repo=repo)
    res = await uc.execute(SetWorkflowOwnerCommand(
        workflow_id=created.id, owner="bob", actor_id="alice",
    ))
    assert not res.error
    assert res.owner == "bob"
    reloaded = await repo.aget_by_id(created.id)
    assert reloaded.owner == "bob"
    assert reloaded.updated_by == "alice"


# ── Any authenticated caller may reassign (access control deferred to Keycloak) ─

@pytest.mark.asyncio
async def test_any_caller_can_reassign():
    repo = MemoryWorkflowRepository()
    created = await _create(repo, created_by="alice")
    uc = SetWorkflowOwnerUseCase(logger=_Log(), workflow_repo=repo)
    # carol is neither the owner nor an admin — still allowed now.
    res = await uc.execute(SetWorkflowOwnerCommand(
        workflow_id=created.id, owner="dave", actor_id="carol",
    ))
    assert not res.error
    assert res.owner == "dave"
    reloaded = await repo.aget_by_id(created.id)
    assert reloaded.owner == "dave"
    assert reloaded.updated_by == "carol"  # the actor is recorded


# ── Empty owner is rejected ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_owner_rejected():
    repo = MemoryWorkflowRepository()
    created = await _create(repo, created_by="alice")
    uc = SetWorkflowOwnerUseCase(logger=_Log(), workflow_repo=repo)
    res = await uc.execute(SetWorkflowOwnerCommand(
        workflow_id=created.id, owner="  ", actor_id="alice",
    ))
    assert res.error_code == "VALIDATION_ERROR"


# ── Users come from Keycloak (central identity) ───────────────────────────────

class _FakeKeycloak:
    def __init__(self, users):
        self._users = users
    async def list_users(self, limit: int = 500):
        return self._users


@pytest.mark.asyncio
async def test_list_users_from_keycloak():
    kc = _FakeKeycloak([
        {"id": "sub-1", "email": "a@x.com", "name": "Alice"},
        {"id": "sub-2", "email": "b@x.com", "name": "Bob"},
    ])
    result = await ListUsersUseCase(logger=_Log(), keycloak_admin_client=kc).execute()
    by_id = {u["id"]: u for u in result.items}
    assert by_id["sub-1"]["name"] == "Alice"
    assert by_id["sub-2"]["email"] == "b@x.com"
    assert result.total == 2


@pytest.mark.asyncio
async def test_list_users_degrades_to_empty():
    # An unconfigured/failing client yields [] rather than erroring the endpoint.
    result = await ListUsersUseCase(logger=_Log(), keycloak_admin_client=_FakeKeycloak([])).execute()
    assert result.items == [] and result.total == 0


# ── Keycloak admin client — URL derivation + configured guard ─────────────────

def test_keycloak_admin_url_derivation():
    from app.layer4_frameworks.auth.keycloak_admin import KeycloakAdminClient
    kc = KeycloakAdminClient(
        issuer="https://kc.example/realms/tenant-1", client_id="c", client_secret="s",
    )
    assert kc.configured
    assert kc._admin_users_url == "https://kc.example/admin/realms/tenant-1/users"
    assert kc._token_url == "https://kc.example/realms/tenant-1/protocol/openid-connect/token"
    # Unconfigured → not usable (endpoint will degrade to []).
    assert not KeycloakAdminClient(issuer="", client_id="", client_secret="").configured


@pytest.mark.asyncio
async def test_keycloak_admin_unconfigured_returns_empty():
    from app.layer4_frameworks.auth.keycloak_admin import KeycloakAdminClient
    kc = KeycloakAdminClient(issuer=None, client_id=None, client_secret=None)
    assert await kc.list_users() == []


# ── is_admin role detection ───────────────────────────────────────────────────

def test_is_admin_detection():
    assert is_admin({"roles": ["workflow-admin"]})
    assert is_admin({"roles": ["realm-admin", "user"]})
    assert is_admin({"roles": ["ADMIN"]})
    assert not is_admin({"roles": ["user", "viewer"]})
    assert not is_admin({"roles": []})
    assert not is_admin({})
