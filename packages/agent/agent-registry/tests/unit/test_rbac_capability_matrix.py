"""T6 (SA-1991) slice 2 — executable capability matrix.

Pins the ticket's default-role -> capability matrix to code along two axes that no other
test covers together:

  A. ROUTE -> required permission code. Every guarded endpoint declares exactly the
     permission the matrix claims (catches a wrong/missing/typo'd guard on any route —
     the automated form of the manual security review).
  B. SEED role -> reachability. Crossing each route's required codes with the *seeded*
     admin/user permission sets reproduces the matrix's per-role columns (user reaches
     reads only; admin reaches everything except the RBAC-config tier; super/system bypass).

If a guard code, a seed grant, or the matrix drifts apart, this fails.
"""
import importlib.util
import os
from contextlib import contextmanager

os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ["DATABASE_SCHEMA"] = "PUBLIC"

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

REG = "/api/v1/agents-registry"
AG = "/api/v1/agents"

# ---- The matrix, transcribed from ticket.md (source of truth) ---------------
# (METHOD, path) -> frozenset of required permission codes. Empty set = authenticated
# only OR authorization handled dynamically inside the use case (documented below).
EXPECTED = {
    # Pools
    ("GET", f"{REG}/agent-pools"): {"pool.read"},
    ("POST", f"{REG}/agent-pools"): {"pool.create"},
    ("GET", f"{REG}/agent-pools/{{pool_id}}"): {"pool.read"},
    ("PUT", f"{REG}/agent-pools/{{pool_id}}"): {"pool.update"},
    ("DELETE", f"{REG}/agent-pools/{{pool_id}}"): {"pool.delete"},
    ("POST", f"{REG}/agent-pools/{{pool_id}}/agents"): {"pool.agent.add"},
    ("DELETE", f"{REG}/agent-pools/{{pool_id}}/agents/{{agent_id}}"): {"pool.agent.remove"},
    ("POST", f"{REG}/agent-pools/{{pool_id}}/members"): {"pool.member.add"},
    ("DELETE", f"{REG}/agent-pools/{{pool_id}}/members/{{user_id}}"): {"pool.member.remove"},
    # Roles / permissions / users
    ("GET", f"{REG}/roles"): {"role.read"},
    ("POST", f"{REG}/roles"): {"role.create"},
    ("PUT", f"{REG}/roles/{{role_id}}"): {"role.update"},
    ("PUT", f"{REG}/roles/{{role_id}}/permissions"): {"role.update_mapping_permission"},
    ("DELETE", f"{REG}/roles/{{role_id}}"): {"role.remove"},
    ("GET", f"{REG}/permissions"): {"permission.view"},
    ("GET", f"{REG}/users"): {"user.read"},
    # Agents — reads (visibility-filtered) + CRUD + lifecycle
    ("GET", f"{AG}/all"): {"agent.read"},
    ("GET", f"{AG}/available"): {"agent.read"},
    ("GET", f"{AG}/filter"): {"agent.read"},
    ("POST", f"{AG}/by_ids"): {"agent.read"},
    ("GET", f"{AG}/{{agent_id}}"): {"agent.read"},
    ("POST", f"{AG}/register"): {"agent.create"},
    ("PUT", f"{AG}/{{agent_id}}"): {"agent.update"},
    ("DELETE", f"{AG}/{{agent_id}}"): {"agent.delete"},
    ("POST", f"{AG}/{{agent_id}}/activate"): {"agent.activate"},
    ("POST", f"{AG}/{{agent_id}}/deactivate"): {"agent.deactivate"},
    ("POST", f"{AG}/{{agent_id}}/publish"): {"agent.publish"},
    ("POST", f"{AG}/{{agent_id}}/unpublish"): {"agent.unpublish"},
}

# Endpoints intentionally WITHOUT a static permission guard:
#  - GET /me           → authenticated only (any principal).
#  - PUT /users/{ext}/role → authorization is the dynamic assign-role transition matrix
#    (R1/R2/R3), which depends on the target's current+next role, so it cannot be a
#    static require_permission. Covered by test_rbac_assign_role.py.
NO_STATIC_GUARD = {
    ("GET", f"{REG}/me"),
    ("PUT", f"{REG}/users/{{external_id}}/role"),
}


def _route_perms(route):
    """Union of required_permissions declared anywhere in a route's dependency tree."""
    out = set()
    dep = getattr(route, "dependant", None)
    stack = list(getattr(dep, "dependencies", []) or [])
    while stack:
        d = stack.pop()
        rp = getattr(getattr(d, "call", None), "required_permissions", None)
        if rp:
            out |= set(rp)
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def _actual_guard_map():
    from bootstrap import create_app
    app = create_app()
    actual = {}
    for r in app.routes:
        path = getattr(r, "path", "")
        if "/agents" not in path:
            continue
        for m in (getattr(r, "methods", None) or set()):
            if m == "HEAD":
                continue
            actual[(m, path)] = _route_perms(r)
    return actual


# ---- Axis A: route -> required permission code ------------------------------
def test_every_guarded_route_matches_the_matrix():
    actual = _actual_guard_map()
    # Each matrix entry is present with EXACTLY its codes (no missing/extra/typo).
    for key, codes in EXPECTED.items():
        assert key in actual, f"route vanished from the app: {key}"
        assert actual[key] == codes, f"{key}: guard {actual[key]} != matrix {codes}"


def test_dynamic_and_public_routes_have_no_static_guard():
    actual = _actual_guard_map()
    for key in NO_STATIC_GUARD:
        assert key in actual, f"expected route missing: {key}"
        assert actual[key] == set(), f"{key} must have no static permission guard"


def test_no_agent_route_is_left_unguarded():
    # Every agent/registry route is either in EXPECTED (guarded) or NO_STATIC_GUARD
    # (deliberately open) — nothing slips through unclassified.
    actual = _actual_guard_map()
    classified = set(EXPECTED) | NO_STATIC_GUARD
    unclassified = {k for k, v in actual.items() if k not in classified}
    assert not unclassified, f"unclassified agent routes (guard drift?): {unclassified}"
    # And no route we expect to be guarded accidentally lost its codes.
    for key in EXPECTED:
        assert actual.get(key), f"{key} lost its guard (now unguarded!)"


# ---- Axis B: seeded role -> reachability reproduces the matrix ---------------
def _load(fname, modname):
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, "app", "layer4_infrastructure", "persistence", "migrations",
                        "versions", fname)
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextmanager
def _seeded_perms():
    """Yield (admin_perms, user_perms) as seeded by the T1 migration."""
    eng = create_engine("sqlite://")
    conn = eng.connect()
    from app.layer4_infrastructure.persistence.models import AgentModel
    AgentModel.__table__.create(conn)
    conn.commit()
    with Operations.context(MigrationContext.configure(conn)):
        _load("20260716_01.py", "cm_matrix_01").upgrade()
    conn.commit()

    def perms(code):
        rows = conn.execute(sa.text(
            "SELECT p.code FROM role_permissions rp "
            "JOIN roles r ON rp.role_id=r.id JOIN permissions p ON rp.permission_id=p.id "
            "WHERE r.code=:c"), {"c": code}).scalars().all()
        return set(rows)

    try:
        yield perms("admin"), perms("user")
    finally:
        conn.close()


def test_seed_reachability_matches_matrix_columns():
    with _seeded_perms() as (admin_perms, user_perms):
        # user (agent.read + pool.read) reaches ONLY the read endpoints, nothing mutating.
        for key, codes in EXPECTED.items():
            method, path = key
            user_ok = codes <= user_perms
            is_read = codes in ({"agent.read"}, {"pool.read"})
            assert user_ok == is_read, f"user reachability wrong for {key}"

        # admin reaches everything EXCEPT the RBAC-config tier (role create/update/
        # mapping/remove + permission.view). It never gets super/custom assign either,
        # but those live on the guard-less /users/{ext}/role route (dynamic).
        admin_denied = {
            ("POST", f"{REG}/roles"), ("PUT", f"{REG}/roles/{{role_id}}"),
            ("PUT", f"{REG}/roles/{{role_id}}/permissions"),
            ("DELETE", f"{REG}/roles/{{role_id}}"),
            ("GET", f"{REG}/permissions"),
        }
        for key, codes in EXPECTED.items():
            admin_ok = codes <= admin_perms
            assert admin_ok == (key not in admin_denied), f"admin reachability wrong for {key}"


def test_admin_lacks_the_privilege_boundary_codes():
    with _seeded_perms() as (admin_perms, _user):
        boundary = {
            "role.create", "role.update", "role.update_mapping_permission", "role.remove",
            "permission.view", "user.role.assign_super_admin_role",
            "user.role.unassign_super_admin_role", "user.role.assign_custom_role",
            "user.role.unassign_custom_role",
        }
        assert admin_perms.isdisjoint(boundary), "admin must not hold RBAC-config/super/custom codes"
