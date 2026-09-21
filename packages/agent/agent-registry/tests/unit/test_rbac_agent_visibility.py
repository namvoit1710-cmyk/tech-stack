"""T5 slice 1 — agent read guard + pool visibility (SA RBAC v1).

Covers the visibility filter helper, the get_visible_agent_ids dependency (bypass vs
pool-lookup), and the use-case hiding paths (which 404 before DTO conversion). No DB.
"""
import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ.setdefault("DATABASE_SCHEMA", "PUBLIC")

import pytest

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.agent_access_filter import filter_agents_by_visibility
from app.layer2_application.use_cases.get_agent_by_id import GetAgentByIdUseCase
from app.layer2_application.use_cases.get_agent_by_ids import GetAgentsByIdsUseCase
from app.layer3_presentation.dependencies.agent_visibility import get_visible_agent_ids


def _ag(i):
    return SimpleNamespace(id=i)


def _run(coro):
    return asyncio.run(coro)


# ---- filter_agents_by_visibility -------------------------------------------
def test_visibility_none_returns_all():
    ags = [_ag("a1"), _ag("a2")]
    assert filter_agents_by_visibility(ags, None) == ags


def test_visibility_restricts_to_set():
    ags = [_ag("a1"), _ag("a2"), _ag("a3")]
    assert [a.id for a in filter_agents_by_visibility(ags, {"a1", "a3"})] == ["a1", "a3"]


def test_visibility_empty_set_hides_all():
    assert filter_agents_by_visibility([_ag("a1")], set()) == []


# ---- get_visible_agent_ids dependency --------------------------------------
class _FakePoolRepo:
    def __init__(self):
        self.called_with = "UNSET"

    def find_agent_ids_visible_to(self, user_id):
        self.called_with = user_id
        return {"a1"}


def _user(perms, is_super=False):
    return Principal.for_user(user_id="u1", external_id="e", role_code="user",
                              is_super=is_super, permissions=frozenset(perms))


def test_visible_ids_super_bypasses():
    repo = _FakePoolRepo()
    assert _run(get_visible_agent_ids(principal=_user(set(), is_super=True), pool_repository=repo)) is None
    assert repo.called_with == "UNSET"  # pool repo not queried on bypass


def test_visible_ids_system_bypasses():
    assert _run(get_visible_agent_ids(principal=Principal.system(), pool_repository=_FakePoolRepo())) is None


def test_visible_ids_view_all_bypasses():
    assert _run(get_visible_agent_ids(principal=_user({"agent.view_all"}), pool_repository=_FakePoolRepo())) is None


def test_visible_ids_plain_user_queries_pools():
    repo = _FakePoolRepo()
    result = _run(get_visible_agent_ids(principal=_user({"agent.read"}), pool_repository=repo))
    assert result == {"a1"} and repo.called_with == "u1"


# ---- use-case hiding (404 before DTO conversion) ---------------------------
class _FakeAgentRepo:
    def __init__(self, agents):
        self._by_id = {a.id: a for a in agents}

    def find_by_id(self, aid):
        return self._by_id.get(aid)

    def find_by_ids(self, ids):
        return [self._by_id[i] for i in ids if i in self._by_id]


def test_get_agent_by_id_hidden_is_404():
    uc = GetAgentByIdUseCase(_FakeAgentRepo([_ag("a1")]))
    with pytest.raises(NotFoundException):
        uc.execute("a1", visible_ids={"a2"})   # exists but not visible → hidden
    with pytest.raises(NotFoundException):
        uc.execute("missing", visible_ids=None)  # genuinely absent


def test_get_agents_by_ids_all_hidden_is_404():
    uc = GetAgentsByIdsUseCase(_FakeAgentRepo([_ag("a1"), _ag("a2")]))
    with pytest.raises(NotFoundException):
        uc.execute(["a1", "a2"], visible_ids={"a3"})  # none visible


# ---- boot smoke ------------------------------------------------------------
def test_app_boots_with_agent_guards():
    from bootstrap import create_app
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/v1/agents/all" in paths and "/api/v1/agents/{agent_id}" in paths
