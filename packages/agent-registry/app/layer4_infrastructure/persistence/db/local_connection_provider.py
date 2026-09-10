"""
Local Connection Provider for local development environment.
Handles connection configuration from environment variables or settings.
"""
import ssl
from typing import Optional
from urllib.parse import quote_plus

from app.layer4_infrastructure.settings import DatabaseSettings
from app.layer4_infrastructure.persistence.db.connection_provider import (
    ConnectionProvider,
    ConnectionConfig,
)
from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)


class LocalConnectionProvider(ConnectionProvider):
    """
    Connection provider for local development environment.
    Retrieves connection details from DatabaseSettings (env variables).
    """
    
    def __init__(self, config: DatabaseSettings):
        """
        Initialize with database configuration.
        
        Args:
            config: DatabaseSettings object with connection parameters
        """
        self.config = config
        self._config: Optional[ConnectionConfig] = None
        
    def is_available(self) -> bool:
        """
        Check if local configuration is available.
        
        Local mode does NOT support HANA Cloud connections (requires SSL certificate).
        If connecting to HANA Cloud, use VCAP mode instead.
        
        Returns:
            bool: True if database config has required fields AND not HANA Cloud
        """
        try:
            # Check required fields
            has_config = bool(
                self.config.database_host
                and self.config.database_port
                and self.config.database_user
                and self.config.database_password
            )
            
            if not has_config:
                return False
            
            # Check if host is HANA Cloud
            if self._is_hana_cloud_host(self.config.database_host):
                return False
            
            return True
            
        except AttributeError as e:
            logger.debug("Local config not available: %s", e)
            return False
    
    @staticmethod
    def _is_hana_cloud_host(host: str) -> bool:
        """
        Check if hostname belongs to HANA Cloud.
        
        Args:
            host: Hostname to check
            
        Returns:
            bool: True if this is a HANA Cloud host
        """
        hana_cloud_patterns = [
            '.hanacloud.ondemand.com',
            'hana.ondemand.com',
            '.hana.prod',
            '.hana.canary',
        ]
        host_lower = host.lower()
        return any(pattern in host_lower for pattern in hana_cloud_patterns)
    
    def get_connection_config(self) -> ConnectionConfig:
        """
        Retrieves connection configuration from DatabaseSettings.
        
        Returns:
            ConnectionConfig: Configuration from environment variables
            
        Raises:
            RuntimeError: If required configuration is missing
        """
        if self._config:
            return self._config
            
        logger.info("Loading connection config from local environment...")
        
        if not self.is_available():
            raise RuntimeError(
                "Local database configuration is incomplete. "
                "Check environment variables: DATABASE_HOST, DATABASE_PORT, DATABASE_USER, DATABASE_PASSWORD"
            )
        
        if not self.config.database_schema:
            raise RuntimeError(
                "Local mode requires DATABASE_SCHEMA. "
                "Check environment variable: DATABASE_SCHEMA"
            )
        
        logger.info(
            "Local config loaded: host=%s, port=%s, database=%s, schema=%s",
            self.config.database_host, self.config.database_port, self.config.database_name, self.config.database_schema
        )
        
        # Store raw credentials - will be URL-encoded in create_engine()
        self._config = ConnectionConfig(
            host=self.config.database_host,
            port=self.config.database_port,
            user=self.config.database_user,  # Raw, not encoded
            password=self.config.database_password.get_secret_value(),  # Raw, not encoded
            schema_name=self.config.database_schema,
            certificate=None,
            database_id=None,
            encrypt=False,
            validate_certificate=False,
        )
        
        return self._config
    
    def get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Creates SSL context for local connection.
        For local development, we typically disable SSL verification.
        
        Returns:
            ssl.SSLContext: SSL context with verification disabled
        """
        logger.info("Creating SSL context for local connection (verification disabled)")
        
        # Create SSL context but disable verification for local development
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        return ssl_context
    
    def create_engine(self, min_connections: int, max_connections: int, database_name: Optional[str] = None):
        """
        Create SQLAlchemy engine for LOCAL mode using connection string.
        Uses the exact same pattern as the original working code.
        
        Args:
            min_connections: Minimum pool size
            max_connections: Maximum pool size
            database_name: Database name (overrides config if provided)
            
        Returns:
            Engine: SQLAlchemy engine configured with connection string
        """
        from sqlalchemy import create_engine
        
        conn_config = self.get_connection_config()
        user = quote_plus(conn_config.user)
        password = quote_plus(conn_config.password)
        db_name = database_name or self.config.database_name
        
        db_uri = (
            f"hana+hdbcli://{user}:{password}@"
            f"{conn_config.host}:{conn_config.port}/{db_name}"
            f"?currentschema={conn_config.schema_name}"
        )
        
        logger.info(
            "LOCAL mode: Creating engine - host=%s, port=%s, database=%s, schema=%s",
            conn_config.host, conn_config.port, db_name, conn_config.schema_name
        )
        
        # Create engine exactly like original working code
        engine = create_engine(
            db_uri,
            pool_size=min_connections,
            max_overflow=max_connections - min_connections,
        )
        
        logger.info("LOCAL mode: Engine created successfully")
        return engine
