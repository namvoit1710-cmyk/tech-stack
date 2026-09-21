"""
Abstract base class defining the contract for database connection providers.
This follows the Strategy Pattern to allow different connection strategies
based on the deployment environment (VCAP vs Local).
"""
from abc import ABC, abstractmethod
from typing import Optional
import ssl

from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)


class ConnectionConfig:
    """Data class to hold connection parameters"""
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        schema_name: Optional[str] = None,
        certificate: Optional[str] = None,
        database_id: Optional[str] = None,
        encrypt: bool = False,
        validate_certificate: bool = False,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.schema_name = schema_name
        self.certificate = certificate
        self.database_id = database_id
        self.encrypt = encrypt
        self.validate_certificate = validate_certificate


class ConnectionProvider(ABC):
    """
    Abstract base class for database connection providers.
    Each implementation handles a specific connection strategy.
    """
    
    @abstractmethod
    def get_connection_config(self) -> ConnectionConfig:
        """
        Retrieves connection configuration from the specific source.
        
        Returns:
            ConnectionConfig: Configuration object with all connection parameters
        """
    
    @abstractmethod
    def get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Creates and returns SSL context for secure connections.
        
        Returns:
            Optional[ssl.SSLContext]: SSL context if SSL is required, None otherwise
        """
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Checks if this provider can be used in the current environment.
        
        Returns:
            bool: True if provider is available, False otherwise
        """
    
    @abstractmethod
    def create_engine(self, min_connections: int, max_connections: int, database_name: Optional[str] = None):
        """
        Creates SQLAlchemy engine with mode-specific logic.
        Each provider implements its own engine creation strategy.
        
        Args:
            min_connections: Minimum pool size
            max_connections: Maximum pool size
            database_name: Optional database name
            
        Returns:
            Engine: SQLAlchemy engine configured for this provider's mode
        """
    
    def log_connection_info(self, config: ConnectionConfig) -> None:
        """
        Logs connection information (safely, without sensitive data).
        
        Args:
            config: Connection configuration to log
        """
        masked_user = config.user[:20] + "..." if len(config.user) > 20 else config.user
        logger.info(
            "Connection config: user=%s, host=%s, port=%s, schema=%s, encrypt=%s, validate_cert=%s",
            masked_user, config.host, config.port, config.schema_name,
            config.encrypt, config.validate_certificate
        )
