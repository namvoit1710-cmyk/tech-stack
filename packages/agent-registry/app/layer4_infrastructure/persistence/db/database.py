"""Database connection factory and session management."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker, Session
from app.layer4_infrastructure.persistence.models.base_model import Base
from app.layer4_infrastructure.persistence.db.connection_provider_factory import ConnectionProviderFactory
from app.layer4_infrastructure.settings import DatabaseSettings


class DatabaseFactory:
    """Factory for creating database connections and sessions."""

    def __init__(self, settings: DatabaseSettings):
        """Initialize database factory.
        
        Args:
            settings: Database settings
        """
        self.settings = settings
        self._engine = None
        self._session_factory = None
        self._connection_provider = ConnectionProviderFactory.create(settings)
        
    def create_engine(self):
        """Create synchronous SQLAlchemy engine.
        
        For SAP HANA, we use the hana+hdbcli:// dialect (synchronous).
        For testing, can use sqlite:///:memory:
        """
        
        engine = self._connection_provider.create_engine(
            min_connections=self.settings.database_pool_size,
            max_connections=self.settings.database_pool_size + self.settings.database_max_overflow,
        )
        
        self._engine = engine
        return engine

    def create_session_factory(self) -> sessionmaker:
        """Create session factory for creating database sessions.
        
        Returns:
            Session maker
        """
        if not self._engine:
            self.create_engine()

        self._session_factory = sessionmaker(
            bind=self._engine,
            class_=Session,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        return self._session_factory

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Get synchronous database session context manager.
        
        Yields:
            Session instance
        """
        if not self._session_factory:
            self.create_session_factory()

        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_tables(self) -> None:
        """Create all database tables.
        
        Use for development/testing. In production, use Alembic migrations.
        """
        if not self._engine:
            self.create_engine()

        with self._engine.begin() as connection:
            self._ensure_schema_exists(connection)
            self._set_current_schema(connection)
            Base.metadata.create_all(bind=connection)

    def _normalized_schema(self, schema_name: str | None) -> str | None:
        """Return the effective schema name, treating PUBLIC as the default schema."""
        if not schema_name or schema_name.upper() == "PUBLIC":
            return None
        return schema_name

    def _ensure_schema_exists(self, connection) -> None:
        """Create the configured schema when it does not already exist."""
        schema_name = self._normalized_schema(self.settings.database_schema)
        if not schema_name:
            return

        inspector = inspect(connection)
        if inspector.has_schema(schema_name):
            return

        connection.execute(text(f'CREATE SCHEMA "{self._quote_identifier(schema_name)}"'))
    
    def _quote_identifier(self, identifier: str) -> str:
            """Escape double quotes for use in quoted SQL identifiers."""
            return identifier.replace('"', '""')

    def _set_current_schema(self, connection) -> None:
        """Ensure DDL runs against the configured schema when one is set."""
        schema_name = self._normalized_schema(self.settings.database_schema)
        if not schema_name:
            return

        connection.execute(text(f'SET SCHEMA "{self._quote_identifier(schema_name)}"'))

    def drop_tables(self) -> None:
        """Drop all database tables.
        
        Use for testing cleanup.
        """
        if not self._engine:
            self.create_engine()

        with self._engine.begin() as connection:
            self._set_current_schema(connection)
            Base.metadata.drop_all(bind=connection)

    def close(self) -> None:
        """Close database engine and connections."""
        if self._engine:
            self._engine.dispose()


def create_database_factory(settings: DatabaseSettings) -> DatabaseFactory:
    """Create database factory instance.
    
    Args:
        settings: Database settings
        
    Returns:
        DatabaseFactory instance
    """
    return DatabaseFactory(settings)
