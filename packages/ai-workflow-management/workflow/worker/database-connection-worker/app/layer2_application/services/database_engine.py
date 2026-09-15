"""Database Engine Factory - Creates SQLAlchemy engines with connection pooling.

This module provides factory methods for creating database connections
to PostgreSQL, MySQL, and SAP HANA databases with proper configuration.
"""

import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

from app.layer1_domain.value_objects.database_type import DatabaseType


@dataclass
class ConnectionConfig:
    """Database connection configuration."""
    
    db_type: DatabaseType
    host: str
    port: int
    database: str
    username: str
    password: str
    
    # Pool settings
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 1800  # 30 minutes
    
    # Query settings
    statement_timeout: int = 30  # seconds
    
    # SSL settings
    ssl_enabled: bool = False
    ssl_ca_cert: Optional[str] = None


@dataclass
class ConnectionResult:
    """Result of connection test."""
    
    success: bool
    error: Optional[str] = None
    server_version: Optional[str] = None
    latency_ms: Optional[float] = None


class DatabaseEngineFactory:
    """Factory for creating SQLAlchemy database engines."""
    
    # Default ports for each database type
    DEFAULT_PORTS = {
        DatabaseType.POSTGRESQL: 5432,
        DatabaseType.MYSQL: 3306,
        DatabaseType.HANA: 30015,
    }
    
    @classmethod
    def create_engine(cls, config: ConnectionConfig) -> Engine:
        """Create a SQLAlchemy engine with connection pooling.
        
        Args:
            config: Connection configuration.
            
        Returns:
            Configured SQLAlchemy Engine.
            
        Raises:
            ValueError: If database type is not supported.
        """
        connection_url = cls._build_connection_url(config)
        connect_args = cls._build_connect_args(config)
        
        engine = create_engine(
            connection_url,
            poolclass=QueuePool,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout=config.pool_timeout,
            pool_recycle=config.pool_recycle,
            pool_pre_ping=True,  # Check connection validity
            connect_args=connect_args,
            echo=False,  # Don't log SQL in production
        )
        
        return engine
    
    @classmethod
    def _build_connection_url(cls, config: ConnectionConfig) -> str:
        """Build database connection URL.
        
        Args:
            config: Connection configuration.
            
        Returns:
            SQLAlchemy connection URL string.
        """
        # URL-encode username and password to handle special characters
        encoded_user = urllib.parse.quote_plus(config.username)
        encoded_pass = urllib.parse.quote_plus(config.password)
        encoded_db = urllib.parse.quote_plus(config.database)
        
        if config.db_type == DatabaseType.POSTGRESQL:
            return (
                f"postgresql+psycopg2://{encoded_user}:{encoded_pass}"
                f"@{config.host}:{config.port}/{encoded_db}"
            )
        
        elif config.db_type == DatabaseType.MYSQL:
            return (
                f"mysql+pymysql://{encoded_user}:{encoded_pass}"
                f"@{config.host}:{config.port}/{encoded_db}"
                f"?charset=utf8mb4"
            )
        
        elif config.db_type == DatabaseType.HANA:
            return (
                f"hana+hdbcli://{encoded_user}:{encoded_pass}"
                f"@{config.host}:{config.port}/{encoded_db}"
            )
        
        else:
            raise ValueError(f"Unsupported database type: {config.db_type}")
    
    @classmethod
    def _build_connect_args(cls, config: ConnectionConfig) -> dict[str, Any]:
        """Build driver-specific connection arguments.
        
        Args:
            config: Connection configuration.
            
        Returns:
            Dictionary of connection arguments.
        """
        args: dict[str, Any] = {}
        
        if config.db_type == DatabaseType.POSTGRESQL:
            # Set statement timeout (in milliseconds for PostgreSQL)
            args["options"] = f"-c statement_timeout={config.statement_timeout * 1000}"
            
            if config.ssl_enabled:
                args["sslmode"] = "require"
                if config.ssl_ca_cert:
                    args["sslrootcert"] = config.ssl_ca_cert
        
        elif config.db_type == DatabaseType.MYSQL:
            # Set connection and read timeout
            args["connect_timeout"] = config.statement_timeout
            args["read_timeout"] = config.statement_timeout
            args["write_timeout"] = config.statement_timeout
            
            if config.ssl_enabled:
                ssl_args = {"ssl": {"ssl_mode": "REQUIRED"}}
                if config.ssl_ca_cert:
                    ssl_args["ssl"]["ca"] = config.ssl_ca_cert
                args.update(ssl_args)
        
        elif config.db_type == DatabaseType.HANA:
            # HANA connection timeout
            args["timeout"] = config.statement_timeout * 1000  # milliseconds
        
        return args
    
    @classmethod
    def test_connection(
        cls,
        config: ConnectionConfig,
        timeout: Optional[int] = None,
    ) -> ConnectionResult:
        """Test database connection with a simple query.
        
        Args:
            config: Connection configuration.
            timeout: Optional timeout override (seconds).
            
        Returns:
            ConnectionResult with success status and details.
        """
        import time
        
        if timeout is not None:
            config.statement_timeout = timeout
        
        try:
            engine = cls.create_engine(config)
            start_time = time.monotonic()
            
            with engine.connect() as conn:
                # Execute simple test query
                result = conn.execute(text("SELECT 1"))
                result.fetchone()
                
                # Try to get server version
                version = cls._get_server_version(conn, config.db_type)
            
            latency_ms = (time.monotonic() - start_time) * 1000
            engine.dispose()
            
            return ConnectionResult(
                success=True,
                server_version=version,
                latency_ms=round(latency_ms, 2),
            )
        
        except SQLAlchemyError as e:
            return ConnectionResult(
                success=False,
                error=cls._sanitize_error(str(e)),
            )
        except Exception as e:
            return ConnectionResult(
                success=False,
                error=cls._sanitize_error(str(e)),
            )
    
    @classmethod
    def _get_server_version(cls, conn: Any, db_type: DatabaseType) -> Optional[str]:
        """Get database server version.
        
        Args:
            conn: Active database connection.
            db_type: Type of database.
            
        Returns:
            Server version string or None.
        """
        try:
            if db_type == DatabaseType.POSTGRESQL:
                result = conn.execute(text("SELECT version()"))
                row = result.fetchone()
                return row[0] if row else None
            
            elif db_type == DatabaseType.MYSQL:
                result = conn.execute(text("SELECT VERSION()"))
                row = result.fetchone()
                return row[0] if row else None
            
            elif db_type == DatabaseType.HANA:
                result = conn.execute(text("SELECT * FROM M_DATABASE"))
                row = result.fetchone()
                return str(row[0]) if row else None
        
        except Exception:
            return None
        
        return None
    
    @classmethod
    def _sanitize_error(cls, error: str) -> str:
        """Sanitize error message to avoid exposing sensitive information.
        
        Args:
            error: Raw error message.
            
        Returns:
            Sanitized error message.
        """
        # Remove potential password/credential patterns
        import re
        
        # Hide password in connection string
        sanitized = re.sub(
            r"://[^:]+:[^@]+@",
            "://***:***@",
            error,
        )
        
        # Truncate very long error messages
        max_length = 500
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "... [TRUNCATED]"
        
        return sanitized
    
    @classmethod
    @contextmanager
    def get_connection(
        cls,
        config: ConnectionConfig,
    ) -> Generator:
        """Context manager for getting a database connection.
        
        Args:
            config: Connection configuration.
            
        Yields:
            Active database connection.
        """
        engine = cls.create_engine(config)
        try:
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
