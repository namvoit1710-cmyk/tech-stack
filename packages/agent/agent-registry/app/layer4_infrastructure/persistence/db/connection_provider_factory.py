"""
Factory for creating appropriate connection provider based on environment.
Implements the Factory Pattern to abstract provider selection logic.
"""
import os


from app.layer4_infrastructure.settings import DatabaseSettings
from app.layer4_infrastructure.persistence.db.connection_provider import ConnectionProvider
from app.layer4_infrastructure.persistence.db.vcap_connection_provider import (
    VCAPConnectionProvider,
)
from app.layer4_infrastructure.persistence.db.local_connection_provider import (
    LocalConnectionProvider,
)
from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)


class ConnectionProviderFactory:
    """
    Factory class to create appropriate connection provider based on environment.
    
    Priority order:
    1. If DB_CONNECTION_MODE env variable is set, use it explicitly
    2. If VCAP_SERVICES is available, use VCAPConnectionProvider
    3. Otherwise, fallback to LocalConnectionProvider
    """
    
    # Environment variable to explicitly set connection mode
    CONNECTION_MODE_ENV = "DB_CONNECTION_MODE"
    
    # Valid connection modes
    MODE_VCAP = "vcap"
    MODE_LOCAL = "local"
    
    @staticmethod
    def create(config: DatabaseSettings) -> ConnectionProvider:
        """
        Creates and returns appropriate connection provider.
        
        Smart auto-detection logic:
        1. If DB_CONNECTION_MODE is set explicitly, use it
        2. If VCAP_SERVICES exists and has HANA service, use VCAP
        3. If VCAP_SERVICES exists but NO HANA service, use Local (running on BTP but not bound)
        4. If no VCAP_SERVICES at all, use Local (local development)
        
        Args:
            config: DatabaseSettings for fallback local connection
            
        Returns:
            ConnectionProvider: Instance of appropriate provider
            
        Raises:
            RuntimeError: If no suitable provider is available
        """
        # Check if connection mode is explicitly set via environment variable
        explicit_mode = os.getenv(ConnectionProviderFactory.CONNECTION_MODE_ENV)
        
        if explicit_mode:
            logger.info("Using explicit connection mode from environment: %s", explicit_mode)
            return ConnectionProviderFactory._create_explicit(explicit_mode, config)
        
        # Auto-detect based on environment
        logger.info("Auto-detecting connection provider...")
        return ConnectionProviderFactory._auto_detect(config)
    
    @staticmethod
    def _create_explicit(mode: str, config: DatabaseSettings) -> ConnectionProvider:
        """
        Creates provider based on explicit mode setting.
        
        Args:
            mode: Connection mode string
            config: DatabaseSettings for local provider
            
        Returns:
            ConnectionProvider: Instance of specified provider
            
        Raises:
            ValueError: If mode is not recognized
        """
        mode = mode.lower().strip()
        
        if mode == ConnectionProviderFactory.MODE_VCAP:
            logger.info("Creating VCAP connection provider (explicit mode)")
            provider = VCAPConnectionProvider()
            if not provider.is_available():
                raise RuntimeError(
                    "VCAP mode requested but VCAP_SERVICES not available. "
                    "Check your environment configuration."
                )
            return provider
            
        elif mode == ConnectionProviderFactory.MODE_LOCAL:
            logger.info("Creating Local connection provider (explicit mode)")
            provider = LocalConnectionProvider(config)
            if not provider.is_available():
                raise RuntimeError(
                    "Local mode requested but database configuration is incomplete. "
                    "Check environment variables: DATABASE_HOST, DATABASE_PORT, DATABASE_USER, DATABASE_PASSWORD"
                )
            return provider
            
        else:
            raise ValueError(
                f"Invalid connection mode: {mode}. "
                f"Valid modes are: {ConnectionProviderFactory.MODE_VCAP}, "
                f"{ConnectionProviderFactory.MODE_LOCAL}"
            )
    
    @staticmethod
    def _auto_detect(config: DatabaseSettings) -> ConnectionProvider:
        """
        Auto-detects and creates appropriate provider with smart logic.
        
        Smart detection logic:
        1. Check if VCAP_SERVICES exists (SAP BTP environment)
        2. If VCAP_SERVICES exists:
           - Check if HANA service binding exists
           - If HANA service exists → use VCAP provider
           - If HANA service NOT exists → fallback to Local (on BTP but not bound to HANA)
        3. If VCAP_SERVICES does not exist:
           - Use Local provider (local development)
        
        This handles the case where app is running on SAP BTP but not bound to HANA service.
        
        Args:
            config: DatabaseConfig for local provider
            
        Returns:
            ConnectionProvider: Instance of available provider
            
        Raises:
            RuntimeError: If no provider is available
        """
        # Try VCAP provider first - this checks both VCAP_SERVICES existence AND HANA service
        vcap_provider = VCAPConnectionProvider()
        if vcap_provider.is_available():
            logger.info(
                "VCAP_SERVICES with HANA service detected - using VCAP connection provider"
            )
            return vcap_provider
        
        # Check if VCAP_SERVICES exists but without HANA service
        # This means we're on SAP BTP but not bound to HANA
        if os.getenv("VCAP_SERVICES"):
            logger.info(
                "VCAP_SERVICES found but no HANA service binding - "
                "falling back to Local connection provider"
            )
        else:
            logger.info(
                "VCAP_SERVICES not found - using Local connection provider for local development"
            )
        
        # Fallback to Local provider
        local_provider = LocalConnectionProvider(config)
        if local_provider.is_available():
            return local_provider
        
        # No provider available - provide detailed error message
        error_msg = "No connection provider available.\n\n"
        raise RuntimeError(error_msg)
    
    @staticmethod
    def get_available_modes() -> list[str]:
        """
        Returns list of available connection modes.
        
        Returns:
            list[str]: List of mode names
        """
        return [
            ConnectionProviderFactory.MODE_VCAP,
            ConnectionProviderFactory.MODE_LOCAL,
        ]
