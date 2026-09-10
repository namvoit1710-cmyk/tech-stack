"""RBAC v1 additive schema: roles/permissions, pools, user-mirror, FK, seed (T1)

Additive-only (B2 expand phase). Creates the dynamic-RBAC tables (roles, permissions,
role_permissions), extends the users mirror (external_id, role_id; email loses UNIQUE),
adds the agent pools (agent_pools + agent_pool_agents + agent_pool_members) with REAL
foreign keys, and seeds the permission catalog + 3 built-in roles + the super_admin user.

No destructive drops here: `agent_users` and the `agents.user_email/tenant_id` columns
are handled later in the T5 contract migration (20260716_02).

NOTE (PR call-out): real FK constraints DIVERGE from the repo's app-level-relationship
convention. Cascade: role_permissions x2 CASCADE, pool link tables CASCADE,
agent_pools.created_by SET NULL, users.role_id plain FK (roles are protected from delete
at the app layer). The users table is handled DEFENSIVELY: ALTER if it already exists
(created by 20260709_01), else CREATE the full mirror.

Revision ID: 20260716_01
Revises: 20260713_01
Create Date: 2026-07-16 00:00:00
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from uuid6 import uuid7

from app.layer4_infrastructure.settings import Settings

settings = Settings()

revision: str = "20260716_01"
down_revision: Union[str, Sequence[str], None] = "20260713_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------- #
# Seed data (single source of truth for the T1 seed)                          #
# --------------------------------------------------------------------------- #

SUPER_ADMIN_ROLE = "super_admin"
ADMIN_ROLE = "admin"
USER_ROLE = "user"

SEED_SUPER_ADMIN_ID = "9e1a4f12-7a29-4f56-aa1a-f0651a777ab6"
SEED_SUPER_ADMIN_EMAIL = "smdg.admin@laidon.com"
SEED_SUPER_ADMIN_TENANT_ID = "e3286482-589b-494b-932e-4103a693a263"

# Permission catalog (30) — (code, description). App-defined; the UI never adds codes.
PERMISSION_CATALOG: list[tuple[str, str]] = [
    # Agents (9)
    ("agent.read", "View/list agents (limited to own pools unless agent.view_all)."),
    ("agent.view_all", "See all agents regardless of pool (bypass visibility filter)."),
    ("agent.create", "Register a new agent."),
    ("agent.update", "Edit an agent."),
    ("agent.delete", "Soft-delete an agent."),
    ("agent.publish", "Publish an agent."),
    ("agent.unpublish", "Unpublish an agent."),
    ("agent.activate", "Activate a published agent."),
    ("agent.deactivate", "Deactivate an agent."),
    # Pools (8)
    ("pool.read", "View/list pools (own pools only unless privileged)."),
    ("pool.create", "Create a pool."),
    ("pool.update", "Rename/edit a pool."),
    ("pool.delete", "Soft-delete a pool (clears its link rows)."),
    ("pool.agent.add", "Attach an agent to a pool."),
    ("pool.agent.remove", "Detach an agent from a pool."),
    ("pool.member.add", "Add a user (member) to a pool."),
    ("pool.member.remove", "Remove a user from a pool."),
    # Users & role assignment (7)
    ("user.read", "View/list the user mirror."),
    ("user.role.assign_admin_role", "Grant the admin role to a user."),
    ("user.role.unassign_admin_role", "Remove the admin role from a user."),
    ("user.role.assign_super_admin_role", "Grant the super_admin role (encodes R2)."),
    ("user.role.unassign_super_admin_role", "Remove the super_admin role (encodes R1)."),
    ("user.role.assign_custom_role", "Grant a custom role to a user (super_admin only)."),
    ("user.role.unassign_custom_role", "Remove a custom role from a user (super_admin only)."),
    # Roles & permissions administration (6)
    ("role.read", "View roles and their permission sets."),
    ("role.create", "Create a custom role."),
    ("role.update", "Edit a role's name/description."),
    ("role.update_mapping_permission", "Edit a role's permission set."),
    ("role.remove", "Delete a custom role."),
    ("permission.view", "View the permission catalog."),
]

# admin (21): all agent.* + all pool.* + user.read + (un)assign_admin_role + role.read
ADMIN_PERMISSIONS: list[str] = [
    "agent.read", "agent.view_all", "agent.create", "agent.update", "agent.delete",
    "agent.publish", "agent.unpublish", "agent.activate", "agent.deactivate",
    "pool.read", "pool.create", "pool.update", "pool.delete",
    "pool.agent.add", "pool.agent.remove", "pool.member.add", "pool.member.remove",
    "user.read", "user.role.assign_admin_role", "user.role.unassign_admin_role",
    "role.read",
]

# user (2)
USER_PERMISSIONS: list[str] = ["agent.read", "pool.read"]


# --------------------------------------------------------------------------- #
# Idempotency helpers (mirror the repo convention)                            #
# --------------------------------------------------------------------------- #

def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str, schema: str | None) -> bool:
    return _inspector().has_table(table_name, schema=schema)


def _column_exists(table_name: str, column_name: str, schema: str | None) -> bool:
    try:
        columns = _inspector().get_columns(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False
    return any(col["name"] == column_name for col in columns)


def _index_exists(table_name: str, index_name: str, schema: str | None) -> bool:
    try:
        indexes = _inspector().get_indexes(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False
    return any(ix["name"] == index_name for ix in indexes)


def _index_is_unique(table_name: str, index_name: str, schema: str | None) -> bool:
    try:
        indexes = _inspector().get_indexes(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False
    return any(ix["name"] == index_name and ix.get("unique") for ix in indexes)


def _fk_exists(table_name: str, fk_name: str, schema: str | None) -> bool:
    try:
        fks = _inspector().get_foreign_keys(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False
    return any(fk.get("name") == fk_name for fk in fks)


def _fk_target(schema: str | None, ref: str) -> str:
    """Schema-qualify a FK target ('roles.id' -> 'SCHEMA.roles.id' when a schema is set)."""
    return f"{schema}.{ref}" if schema else ref


# Lightweight table handles for idempotent seeding (schema-aware). ------------ #

def _roles_table(schema: str | None):
    return sa.table(
        "roles",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_system", sa.Boolean),
        sa.column("is_super", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        schema=schema,
    )


def _permissions_table(schema: str | None):
    return sa.table(
        "permissions",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("description", sa.String),
        schema=schema,
    )


def _role_permissions_table(schema: str | None):
    return sa.table(
        "role_permissions",
        sa.column("role_id", sa.String),
        sa.column("permission_id", sa.String),
        schema=schema,
    )


def _users_table(schema: str | None):
    return sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("external_id", sa.String),
        sa.column("email", sa.String),
        sa.column("name", sa.String),
        sa.column("role_id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        schema=schema,
    )


# --------------------------------------------------------------------------- #
# upgrade                                                                      #
# --------------------------------------------------------------------------- #

def upgrade() -> None:
    schema = _normalized_schema()

    _create_rbac_tables(schema)
    _seed_permissions(schema)
    _seed_roles(schema)
    _seed_role_permissions(schema)
    _upgrade_users_mirror(schema)
    _backfill_user_roles(schema)
    _seed_super_admin_user(schema)
    _create_pool_tables(schema)


def _create_rbac_tables(schema: str | None) -> None:
    if not _table_exists("roles", schema):
        op.create_table(
            "roles",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("code", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.Column("is_system", sa.Boolean(), nullable=False),
            sa.Column("is_super", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
    if not _index_exists("roles", "ix_roles_code", schema):
        op.create_index("ix_roles_code", "roles", ["code"], unique=True, schema=schema)

    if not _table_exists("permissions", schema):
        op.create_table(
            "permissions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("code", sa.String(length=100), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
    if not _index_exists("permissions", "ix_permissions_code", schema):
        op.create_index(
            "ix_permissions_code", "permissions", ["code"], unique=True, schema=schema
        )

    if not _table_exists("role_permissions", schema):
        op.create_table(
            "role_permissions",
            sa.Column("role_id", sa.String(length=36), nullable=False),
            sa.Column("permission_id", sa.String(length=36), nullable=False),
            sa.ForeignKeyConstraint(
                ["role_id"], [_fk_target(schema, "roles.id")],
                name="fk_role_permissions_role_id", ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["permission_id"], [_fk_target(schema, "permissions.id")],
                name="fk_role_permissions_permission_id", ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("role_id", "permission_id"),
            schema=schema,
        )


def _upgrade_users_mirror(schema: str | None) -> None:
    """DEFENSIVE: create the full users mirror if absent, else ALTER the existing table."""
    if not _table_exists("users", schema):
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("external_id", sa.String(length=255), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("role_id", sa.String(length=36), nullable=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["role_id"], [_fk_target(schema, "roles.id")], name="fk_users_role_id",
            ),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
        op.create_index(
            "ix_users_external_id", "users", ["external_id"], unique=True, schema=schema
        )
        op.create_index("ix_users_email", "users", ["email"], unique=False, schema=schema)
        op.create_index("ix_users_tenant_id", "users", ["tenant_id"], unique=False, schema=schema)
        return

    # ALTER existing users (created by 20260709_01). batch_alter_table keeps the FK/column
    # work portable: HANA -> in-place ALTERs; SQLite -> copy-and-move (test harness).
    need_external = not _column_exists("users", "external_id", schema)
    need_role = not _column_exists("users", "role_id", schema)
    need_fk = not _fk_exists("users", "fk_users_role_id", schema)
    demote_email = _index_is_unique("users", "ix_users_email", schema)

    if need_external or need_role or need_fk or demote_email:
        with op.batch_alter_table("users", schema=schema) as batch_op:
            if need_external:
                batch_op.add_column(sa.Column("external_id", sa.String(length=255), nullable=True))
            if need_role:
                batch_op.add_column(sa.Column("role_id", sa.String(length=36), nullable=True))
            if need_fk:
                batch_op.create_foreign_key(
                    "fk_users_role_id", "roles", ["role_id"], ["id"], referent_schema=schema
                )
            if demote_email:
                # email loses its UNIQUE (external_id is the key).
                batch_op.drop_index("ix_users_email")

    if not _index_exists("users", "ix_users_external_id", schema):
        op.create_index(
            "ix_users_external_id", "users", ["external_id"], unique=True, schema=schema
        )
    if not _index_exists("users", "ix_users_email", schema):
        op.create_index("ix_users_email", "users", ["email"], unique=False, schema=schema)


def _create_pool_tables(schema: str | None) -> None:
    if not _table_exists("agent_pools", schema):
        op.create_table(
            "agent_pools",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["created_by"], [_fk_target(schema, "users.id")],
                name="fk_agent_pools_created_by", ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )

    if not _table_exists("agent_pool_agents", schema):
        op.create_table(
            "agent_pool_agents",
            sa.Column("pool_id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["pool_id"], [_fk_target(schema, "agent_pools.id")],
                name="fk_agent_pool_agents_pool_id", ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"], [_fk_target(schema, "agents.id")],
                name="fk_agent_pool_agents_agent_id", ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("pool_id", "agent_id"),
            schema=schema,
        )
    if not _index_exists("agent_pool_agents", "ix_agent_pool_agents_agent_id", schema):
        op.create_index(
            "ix_agent_pool_agents_agent_id", "agent_pool_agents", ["agent_id"],
            unique=False, schema=schema,
        )

    if not _table_exists("agent_pool_members", schema):
        op.create_table(
            "agent_pool_members",
            sa.Column("pool_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["pool_id"], [_fk_target(schema, "agent_pools.id")],
                name="fk_agent_pool_members_pool_id", ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], [_fk_target(schema, "users.id")],
                name="fk_agent_pool_members_user_id", ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("pool_id", "user_id"),
            schema=schema,
        )
    if not _index_exists("agent_pool_members", "ix_agent_pool_members_user_id", schema):
        op.create_index(
            "ix_agent_pool_members_user_id", "agent_pool_members", ["user_id"],
            unique=False, schema=schema,
        )


# --------------------------------------------------------------------------- #
# Seed (idempotent — guarded by unique code / id)                             #
# --------------------------------------------------------------------------- #

def _seed_permissions(schema: str | None) -> None:
    bind = op.get_bind()
    perms = _permissions_table(schema)
    existing = set(bind.execute(sa.select(perms.c.code)).scalars().all())
    rows = [
        {"id": str(uuid7()), "code": code, "description": desc}
        for code, desc in PERMISSION_CATALOG
        if code not in existing
    ]
    if rows:
        op.bulk_insert(perms, rows)


def _seed_roles(schema: str | None) -> None:
    bind = op.get_bind()
    roles = _roles_table(schema)
    existing = set(bind.execute(sa.select(roles.c.code)).scalars().all())
    now = datetime.now(timezone.utc)
    seed = [
        (SUPER_ADMIN_ROLE, "Super Admin", "Full access; locked; always all permissions.", True, True),
        (ADMIN_ROLE, "Admin", "Operate agents/pools; grant the admin role.", True, False),
        (USER_ROLE, "User", "Default role; read/use scoped to own pools.", True, False),
    ]
    rows = [
        {
            "id": str(uuid7()), "code": code, "name": name, "description": desc,
            "is_system": is_system, "is_super": is_super,
            "created_at": now, "updated_at": now,
        }
        for code, name, desc, is_system, is_super in seed
        if code not in existing
    ]
    if rows:
        op.bulk_insert(roles, rows)


def _role_ids_by_code(bind, schema: str | None) -> dict[str, str]:
    roles = _roles_table(schema)
    return {row.code: row.id for row in bind.execute(sa.select(roles.c.id, roles.c.code))}


def _permission_ids_by_code(bind, schema: str | None) -> dict[str, str]:
    perms = _permissions_table(schema)
    return {row.code: row.id for row in bind.execute(sa.select(perms.c.id, perms.c.code))}


def _seed_role_permissions(schema: str | None) -> None:
    bind = op.get_bind()
    role_ids = _role_ids_by_code(bind, schema)
    perm_ids = _permission_ids_by_code(bind, schema)
    rp = _role_permissions_table(schema)

    existing = {
        (row.role_id, row.permission_id)
        for row in bind.execute(sa.select(rp.c.role_id, rp.c.permission_id))
    }

    wanted: list[tuple[str, str]] = []  # (role_code, perm_code)
    wanted += [(ADMIN_ROLE, p) for p in ADMIN_PERMISSIONS]
    wanted += [(USER_ROLE, p) for p in USER_PERMISSIONS]
    # super_admin is is_super -> ALL permissions implicitly (bypass); nothing stored.

    rows = []
    for role_code, perm_code in wanted:
        role_id = role_ids.get(role_code)
        perm_id = perm_ids.get(perm_code)
        if role_id and perm_id and (role_id, perm_id) not in existing:
            rows.append({"role_id": role_id, "permission_id": perm_id})
    if rows:
        op.bulk_insert(rp, rows)


def _backfill_user_roles(schema: str | None) -> None:
    """Existing users with no role get the default 'user' role."""
    bind = op.get_bind()
    role_ids = _role_ids_by_code(bind, schema)
    user_role_id = role_ids.get(USER_ROLE)
    if not user_role_id:
        return
    users = _users_table(schema)
    bind.execute(
        sa.update(users).where(users.c.role_id.is_(None)).values(role_id=user_role_id)
    )


def _seed_super_admin_user(schema: str | None) -> None:
    """Insert the seed super_admin user if absent; else force its role to super_admin."""
    bind = op.get_bind()
    role_ids = _role_ids_by_code(bind, schema)
    super_role_id = role_ids.get(SUPER_ADMIN_ROLE)
    if not super_role_id:
        return

    users = _users_table(schema)
    exists = bind.execute(
        sa.select(users.c.id).where(users.c.id == SEED_SUPER_ADMIN_ID)
    ).first()
    now = datetime.now(timezone.utc)

    if exists:
        bind.execute(
            sa.update(users)
            .where(users.c.id == SEED_SUPER_ADMIN_ID)
            .values(role_id=super_role_id, external_id=SEED_SUPER_ADMIN_ID, updated_at=now)
        )
    else:
        op.bulk_insert(
            users,
            [{
                "id": SEED_SUPER_ADMIN_ID,
                "external_id": SEED_SUPER_ADMIN_ID,
                "email": SEED_SUPER_ADMIN_EMAIL,
                "name": SEED_SUPER_ADMIN_EMAIL,
                "role_id": super_role_id,
                "tenant_id": SEED_SUPER_ADMIN_TENANT_ID,
                "created_at": now,
                "updated_at": now,
            }],
        )


# --------------------------------------------------------------------------- #
# downgrade — fully reverses T1 (additive-only, so no data loss beyond T1's)   #
# `agent_users` and the `agents` columns are untouched (owned by T5).          #
# --------------------------------------------------------------------------- #

def downgrade() -> None:
    schema = _normalized_schema()

    # Pools (drop children first).
    if _index_exists("agent_pool_members", "ix_agent_pool_members_user_id", schema):
        op.drop_index("ix_agent_pool_members_user_id", table_name="agent_pool_members", schema=schema)
    if _table_exists("agent_pool_members", schema):
        op.drop_table("agent_pool_members", schema=schema)

    if _index_exists("agent_pool_agents", "ix_agent_pool_agents_agent_id", schema):
        op.drop_index("ix_agent_pool_agents_agent_id", table_name="agent_pool_agents", schema=schema)
    if _table_exists("agent_pool_agents", schema):
        op.drop_table("agent_pool_agents", schema=schema)

    if _table_exists("agent_pools", schema):
        op.drop_table("agent_pools", schema=schema)

    # users mirror: remove the seed row and the columns/indexes T1 added (keep the table).
    if _table_exists("users", schema):
        users = _users_table(schema)
        op.get_bind().execute(sa.delete(users).where(users.c.id == SEED_SUPER_ADMIN_ID))

        # drop the external_id index before the column (portable), then batch-drop the
        # FK + columns (HANA -> in-place ALTERs; SQLite -> copy-and-move).
        if _index_exists("users", "ix_users_external_id", schema):
            op.drop_index("ix_users_external_id", table_name="users", schema=schema)

        has_fk = _fk_exists("users", "fk_users_role_id", schema)
        has_role = _column_exists("users", "role_id", schema)
        has_external = _column_exists("users", "external_id", schema)
        if has_fk or has_role or has_external:
            with op.batch_alter_table("users", schema=schema) as batch_op:
                if has_fk:
                    batch_op.drop_constraint("fk_users_role_id", type_="foreignkey")
                if has_role:
                    batch_op.drop_column("role_id")
                if has_external:
                    batch_op.drop_column("external_id")

        # restore email UNIQUE
        if _index_exists("users", "ix_users_email", schema) and not _index_is_unique(
            "users", "ix_users_email", schema
        ):
            op.drop_index("ix_users_email", table_name="users", schema=schema)
        if not _index_exists("users", "ix_users_email", schema):
            op.create_index("ix_users_email", "users", ["email"], unique=True, schema=schema)

    # RBAC tables.
    if _table_exists("role_permissions", schema):
        op.drop_table("role_permissions", schema=schema)
    if _index_exists("permissions", "ix_permissions_code", schema):
        op.drop_index("ix_permissions_code", table_name="permissions", schema=schema)
    if _table_exists("permissions", schema):
        op.drop_table("permissions", schema=schema)
    if _index_exists("roles", "ix_roles_code", schema):
        op.drop_index("ix_roles_code", table_name="roles", schema=schema)
    if _table_exists("roles", schema):
        op.drop_table("roles", schema=schema)
