"""RBAC domain constants (SA RBAC v1 — Task 2).

Role codes and the permission codes this layer needs to reason about a Principal.
The full 30-code permission catalog is app-defined and seeded by the T1 migration
(`20260716_01`) — that migration remains the canonical source of the catalog. The
constants here are the subset the domain/principal logic references directly, plus
the built-in role codes. Later tasks (T3–T5) may promote the full catalog into a
shared constant if the guards need it.
"""

# --- Built-in role codes (seeded by T1) -------------------------------------
SUPER_ADMIN_ROLE = "super_admin"
ADMIN_ROLE = "admin"
USER_ROLE = "user"  # default role for a newly mirrored user

# --- Permission codes referenced by principal/visibility logic --------------
AGENT_READ = "agent.read"
AGENT_VIEW_ALL = "agent.view_all"  # bypasses the pool-visibility filter
POOL_READ = "pool.read"

# --- Pool management permission codes (guard the agent-pools router) ---------
# Referencing these named constants (instead of string literals in the router)
# makes a route↔catalog typo a static error rather than a silent authz gap —
# a mistyped code would demand a permission no seeded role holds, quietly
# narrowing the endpoint to super_admin/system (bypass). Kept in sync with the
# T1 seed catalog (`20260716_01`), which remains the canonical source.
POOL_CREATE = "pool.create"
POOL_UPDATE = "pool.update"
POOL_DELETE = "pool.delete"
POOL_AGENT_ADD = "pool.agent.add"
POOL_AGENT_REMOVE = "pool.agent.remove"
POOL_MEMBER_ADD = "pool.member.add"
POOL_MEMBER_REMOVE = "pool.member.remove"

# The seeded permission set of the built-in `user` role. Used only as a last-resort
# fallback when resolving a transient principal while both the mirror row is absent
# AND Profile Management is unreachable (so the DB-backed role lookup is preferred;
# see ResolvePrincipalUseCase). Kept in sync with the T1 seed.
DEFAULT_USER_PERMISSIONS = frozenset({AGENT_READ, POOL_READ})
