import os
from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
import sqlalchemy as sa
from sqlalchemy import create_engine, engine_from_config, pool

from app.layer4_infrastructure.persistence.models.base_model import Base
from app.layer4_infrastructure.persistence.models.agent_model import AgentModel
from app.layer4_infrastructure.persistence.models.agent_tool_model import AgentToolModel
from app.layer4_infrastructure.persistence.models.agent_workflow_model import AgentWorkflowModel
from app.layer4_infrastructure.persistence.models.tool_model import ToolModel
from app.layer4_infrastructure.persistence.models.workflow_model import WorkflowModel
from app.layer4_infrastructure.persistence.models.user_model import UserModel
# RBAC v1 models (register on Base.metadata for autogenerate/target_metadata)
from app.layer4_infrastructure.persistence.models.role_model import RoleModel
from app.layer4_infrastructure.persistence.models.permission_model import PermissionModel
from app.layer4_infrastructure.persistence.models.role_permission_model import RolePermissionModel
from app.layer4_infrastructure.persistence.models.agent_pool_model import AgentPoolModel
from app.layer4_infrastructure.persistence.models.agent_pool_agent_model import AgentPoolAgentModel
from app.layer4_infrastructure.persistence.models.agent_pool_member_model import AgentPoolMemberModel
from app.layer4_infrastructure.settings import Settings

config = context.config
ALEMBIC_DATABASE_URL_ENV = "ALEMBIC_DATABASE_URL"

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Load settings for potential use in migrations
settings = Settings()


def _normalized_schema() -> str | None:
    schema = settings.database_schema
    return None if not schema or schema.upper() == "PUBLIC" else schema


def _context_config_kwargs() -> dict:
    schema = _normalized_schema()
    kwargs = {
        "target_metadata": target_metadata,
        # SAP HANA on BTP may reject Alembic's transactional DDL bootstrap
        # statement when the driver connection is already in auto-commit mode.
        "transactional_ddl": False,
    }

    if schema:
        kwargs["version_table_schema"] = schema
        kwargs["include_schemas"] = True

    return kwargs


def _ensure_schema_exists(connection) -> None:
    schema = _normalized_schema()
    if not schema:
        return

    inspector = sa.inspect(connection)
    if inspector.has_schema(schema):
        return

    quoted_schema_name = schema.replace('"', '""')
    connection.execute(sa.text(f'CREATE SCHEMA "{quoted_schema_name}"'))


def _set_current_schema(connection) -> None:
    schema = _normalized_schema()
    if not schema:
        return

    quoted_schema_name = schema.replace('"', '""')
    connection.execute(sa.text(f'SET SCHEMA "{quoted_schema_name}"'))


def _get_explicit_migration_url() -> str | None:
    explicit_url = os.getenv(ALEMBIC_DATABASE_URL_ENV)
    if not explicit_url:
        return None

    config.set_main_option("sqlalchemy.url", explicit_url.replace("%", "%%"))
    return explicit_url


def _create_provider():
    from app.layer4_infrastructure.persistence.db.connection_provider_factory import (
        ConnectionProviderFactory,
    )

    return ConnectionProviderFactory.create(settings.get_database_settings())


def _create_schema_bootstrap_engine(provider):
    conn_config = provider.get_connection_config()

    if provider.__class__.__name__ == "LocalConnectionProvider":
        database_name = settings.database_name or ""
        database_path = f"/{database_name}" if database_name else ""
        user = quote_plus(conn_config.user)
        password = quote_plus(conn_config.password)
        url = (
            f"hana+hdbcli://{user}:{password}@"
            f"{conn_config.host}:{conn_config.port}{database_path}"
        )
        return create_engine(url, poolclass=pool.NullPool)

    if provider.__class__.__name__ == "VCAPConnectionProvider":
        from hdbcli import dbapi

        ssl_context = provider.get_ssl_context()

        def create_vcap_connection():
            conn_params = {
                "address": conn_config.host,
                "port": conn_config.port,
                "user": conn_config.user,
                "password": conn_config.password,
                "encrypt": conn_config.encrypt,
                "autocommit": False,
            }

            if ssl_context:
                conn_params["sslContext"] = ssl_context

            if conn_config.encrypt:
                conn_params["sslValidateCertificate"] = conn_config.validate_certificate

            return dbapi.connect(**conn_params)

        return create_engine(
            "hana+hdbcli://",
            creator=create_vcap_connection,
            poolclass=pool.NullPool,
        )

    return provider.create_engine(
        min_connections=1,
        max_connections=1,
        database_name=settings.database_name or None,
    )


def _resolve_offline_migration_url() -> str:
    explicit_url = _get_explicit_migration_url()
    if explicit_url:
        return explicit_url

    provider = _create_provider()
    if provider.__class__.__name__ == "VCAPConnectionProvider":
        raise RuntimeError(
            "Offline Alembic migrations on BTP require ALEMBIC_DATABASE_URL "
            "to point at a migration-safe HDI/deployer connection."
        )

    url = settings.database_url
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return url


def _create_migration_engine():
    explicit_url = _get_explicit_migration_url()
    if explicit_url:
        return engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    provider = _create_provider()
    return _create_schema_bootstrap_engine(provider)


def run_migrations_offline() -> None:
    url = _resolve_offline_migration_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_context_config_kwargs(),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = _create_migration_engine()

    with connectable.connect() as connection:
        _ensure_schema_exists(connection)
        _set_current_schema(connection)
        context.configure(connection=connection, **_context_config_kwargs())

        with context.begin_transaction():
            context.run_migrations()

        if connection.in_transaction():
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()