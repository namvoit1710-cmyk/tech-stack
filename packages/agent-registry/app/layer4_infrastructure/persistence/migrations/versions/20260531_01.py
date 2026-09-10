"""initial schema for agent registry

Revision ID: 20260531_01
Revises:
Create Date: 2026-05-31 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.layer4_infrastructure.settings import Settings

# Load settings for potential use in migrations
settings = Settings()


revision: str = "20260531_01"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _ensure_schema_exists(schema: str | None) -> None:
    if not schema:
        return

    inspector = sa.inspect(op.get_bind())
    if inspector.has_schema(schema):
        return

    quoted_schema_name = schema.replace('"', '""')
    op.execute(sa.text(f'CREATE SCHEMA "{quoted_schema_name}"'))


def _table_exists(table_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(table_name, schema=schema)


def _index_exists(table_name: str, index_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
    except sa.exc.NoSuchTableError:
        return False
    return any(index["name"] == index_name for index in indexes)


def _ensure_index(
    table_name: str,
    index_name: str,
    columns: list[str],
    schema: str | None,
    *,
    unique: bool = False,
) -> None:
    if _index_exists(table_name, index_name, schema):
        return

    op.create_index(index_name, table_name, columns, unique=unique, schema=schema)


def upgrade() -> None:
    schema = _normalized_schema()
    _ensure_schema_exists(schema)

    qualified_agents = f"{schema}.agents.id" if schema else "agents.id"
    qualified_tools = f"{schema}.tools.id" if schema else "tools.id"
    qualified_workflows = f"{schema}.workflows.id" if schema else "workflows.id"

    if not _table_exists("agents", schema):
        op.create_table(
            "agents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("is_published", sa.Boolean(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("healthcheck_endpoint", sa.String(length=500), nullable=True),
            sa.Column("invoke_endpoint", sa.String(length=500), nullable=True),
            sa.Column("is_alive", sa.Boolean(), nullable=False),
            sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
            sa.Column("version", sa.String(length=50), nullable=False),
            sa.Column("agent_metadata", sa.Text(), nullable=True),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("model", sa.String(length=255), nullable=False),
            sa.Column("temperature", sa.Float(), nullable=False),
            sa.Column("max_tokens", sa.Integer(), nullable=False),
            sa.Column("system_prompt", sa.Text(), nullable=True),
            sa.Column("config_type", sa.String(length=20), nullable=False),
            sa.Column("timeout_ms", sa.Integer(), nullable=False),
            sa.Column("max_concurrency", sa.Integer(), nullable=False),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("streaming_supported", sa.Boolean(), nullable=False),
            sa.Column("capabilities", sa.Text(), nullable=True),
            sa.Column("attached_agent_ids", sa.Text(), nullable=True),
            sa.Column("knowledge_base", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
        print("Created 'agents' table")

    _ensure_index("agents", "ix_agents_name", ["name"], schema, unique=True)
    _ensure_index("agents", "ix_agents_kind", ["kind"], schema)
    _ensure_index("agents", "ix_agents_status", ["status"], schema)
    _ensure_index("agents", "ix_agents_is_published", ["is_published"], schema)
    _ensure_index("agents", "ix_agents_is_alive", ["is_alive"], schema)
    _ensure_index("agents", "ix_agents_kind_status", ["kind", "status"], schema)
    _ensure_index("agents", "ix_agents_is_alive_kind", ["is_alive", "kind"], schema)

    if not _table_exists("tools", schema):
        op.create_table(
            "tools",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("protocol", sa.String(length=50), nullable=False),
            sa.Column("endpoint", sa.String(length=500), nullable=True),
            sa.Column("parameters_schema", sa.Text(), nullable=True),
            sa.Column("response_schema", sa.Text(), nullable=True),
            sa.Column("auth_config", sa.Text(), nullable=True),
            sa.Column("version", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("entity_metadata", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
        print("Created 'tools' table")
        
    _ensure_index("tools", "ix_tools_name", ["name"], schema, unique=True)
    _ensure_index("tools", "ix_tools_protocol", ["protocol"], schema)
    _ensure_index("tools", "ix_tools_status", ["status"], schema)

    if not _table_exists("workflows", schema):
        op.create_table(
            "workflows",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("version", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )

    _ensure_index("workflows", "ix_workflows_name", ["name"], schema, unique=True)
    _ensure_index("workflows", "ix_workflows_version", ["version"], schema)
    _ensure_index("workflows", "ix_workflows_status", ["status"], schema)

    if not _table_exists("agent_tools", schema):
        op.create_table(
            "agent_tools",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("tool_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], [qualified_agents]),
            sa.ForeignKeyConstraint(["tool_id"], [qualified_tools]),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )
        print("Created 'agent_tools' table")

    _ensure_index("agent_tools", "ix_agent_tools_agent_id", ["agent_id"], schema)
    _ensure_index("agent_tools", "ix_agent_tools_tool_id", ["tool_id"], schema)
    _ensure_index(
        "agent_tools",
        "ix_agent_tool_unique",
        ["agent_id", "tool_id"],
        schema,
        unique=True,
    )

    if not _table_exists("agent_workflows", schema):
        op.create_table(
            "agent_workflows",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("workflow_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], [qualified_agents]),
            sa.ForeignKeyConstraint(["workflow_id"], [qualified_workflows]),
            sa.PrimaryKeyConstraint("id"),
            schema=schema,
        )

    _ensure_index("agent_workflows", "ix_agent_workflows_agent_id", ["agent_id"], schema)
    _ensure_index(
        "agent_workflows",
        "ix_agent_workflows_workflow_id",
        ["workflow_id"],
        schema,
    )
    _ensure_index(
        "agent_workflows",
        "ix_agent_workflow_unique",
        ["agent_id", "workflow_id"],
        schema,
        unique=True,
    )


def downgrade() -> None:
    schema = _normalized_schema()

    if _table_exists("agent_workflows", schema):
        if _index_exists("agent_workflows", "ix_agent_workflow_unique", schema):
            op.drop_index("ix_agent_workflow_unique", table_name="agent_workflows", schema=schema)
        if _index_exists("agent_workflows", "ix_agent_workflows_workflow_id", schema):
            op.drop_index(
                "ix_agent_workflows_workflow_id",
                table_name="agent_workflows",
                schema=schema,
            )
        if _index_exists("agent_workflows", "ix_agent_workflows_agent_id", schema):
            op.drop_index(
                "ix_agent_workflows_agent_id",
                table_name="agent_workflows",
                schema=schema,
            )
        op.drop_table("agent_workflows", schema=schema)

    if _table_exists("agent_tools", schema):
        if _index_exists("agent_tools", "ix_agent_tool_unique", schema):
            op.drop_index("ix_agent_tool_unique", table_name="agent_tools", schema=schema)
        if _index_exists("agent_tools", "ix_agent_tools_tool_id", schema):
            op.drop_index("ix_agent_tools_tool_id", table_name="agent_tools", schema=schema)
        if _index_exists("agent_tools", "ix_agent_tools_agent_id", schema):
            op.drop_index("ix_agent_tools_agent_id", table_name="agent_tools", schema=schema)
        op.drop_table("agent_tools", schema=schema)

    if _table_exists("workflows", schema):
        if _index_exists("workflows", "ix_workflows_status", schema):
            op.drop_index("ix_workflows_status", table_name="workflows", schema=schema)
        if _index_exists("workflows", "ix_workflows_version", schema):
            op.drop_index("ix_workflows_version", table_name="workflows", schema=schema)
        if _index_exists("workflows", "ix_workflows_name", schema):
            op.drop_index("ix_workflows_name", table_name="workflows", schema=schema)
        op.drop_table("workflows", schema=schema)

    if _table_exists("tools", schema):
        if _index_exists("tools", "ix_tools_status", schema):
            op.drop_index("ix_tools_status", table_name="tools", schema=schema)
        if _index_exists("tools", "ix_tools_protocol", schema):
            op.drop_index("ix_tools_protocol", table_name="tools", schema=schema)
        if _index_exists("tools", "ix_tools_name", schema):
            op.drop_index("ix_tools_name", table_name="tools", schema=schema)
        op.drop_table("tools", schema=schema)

    if _table_exists("agents", schema):
        if _index_exists("agents", "ix_agents_is_alive_kind", schema):
            op.drop_index("ix_agents_is_alive_kind", table_name="agents", schema=schema)
        if _index_exists("agents", "ix_agents_kind_status", schema):
            op.drop_index("ix_agents_kind_status", table_name="agents", schema=schema)
        if _index_exists("agents", "ix_agents_is_alive", schema):
            op.drop_index("ix_agents_is_alive", table_name="agents", schema=schema)
        if _index_exists("agents", "ix_agents_is_published", schema):
            op.drop_index("ix_agents_is_published", table_name="agents", schema=schema)
        if _index_exists("agents", "ix_agents_status", schema):
            op.drop_index("ix_agents_status", table_name="agents", schema=schema)
        if _index_exists("agents", "ix_agents_kind", schema):
            op.drop_index("ix_agents_kind", table_name="agents", schema=schema)
        if _index_exists("agents", "ix_agents_name", schema):
            op.drop_index("ix_agents_name", table_name="agents", schema=schema)
        op.drop_table("agents", schema=schema)