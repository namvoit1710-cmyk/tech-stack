"""
VCAP Connection Provider for SAP BTP deployment.
Handles connection configuration from VCAP_SERVICES (Cloud Foundry environment).
"""
import os
import ssl
from typing import Optional

from cfenv import AppEnv

from app.layer4_infrastructure.vcap_service_loader import VCAPServiceLoader
from app.layer4_infrastructure.persistence.db.connection_provider import (
    ConnectionProvider,
    ConnectionConfig,
)
from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)


class VCAPConnectionProvider(ConnectionProvider):
    """
    Connection provider for SAP BTP environments.
    Retrieves connection details from VCAP_SERVICES.
    """
    
    def __init__(self):
        self._config: Optional[ConnectionConfig] = None
        self._env: Optional[AppEnv] = None

    @staticmethod
    def _resolve_validate_certificate() -> bool:
        """Resolve certificate validation mode from environment.

        Defaults to validation enabled for production safety. Local debug flows
        can explicitly disable validation with HANA_SSL_VALIDATE_CERTIFICATE=false.
        """
        raw_value = os.getenv("HANA_SSL_VALIDATE_CERTIFICATE")
        if raw_value is None:
            return True

        return raw_value.strip().lower() not in {"0", "false", "no", "off"}
        
    def is_available(self) -> bool:
        """
        Check if VCAP_SERVICES contains HANA service binding.
        
        Returns:
            bool: True if HANA service is available in VCAP_SERVICES
        """
        try:
            VCAPServiceLoader.load_vcap_service(True)
            env = AppEnv()
            hana_service = env.get_service(label='hana')
            return hana_service is not None
        except (RuntimeError, ValueError, KeyError) as e:
            logger.debug("VCAP service not available: %s", e)
            return False
    
    def get_connection_config(self) -> ConnectionConfig:
        """
        Retrieves connection configuration from VCAP_SERVICES.
        
        Returns:
            ConnectionConfig: Configuration extracted from VCAP_SERVICES
            
        Raises:
            RuntimeError: If HANA service binding is not found
        """
        if self._config:
            return self._config
            
        logger.info("Loading connection config from VCAP_SERVICES...")
        VCAPServiceLoader.load_vcap_service(True)
        env = AppEnv()
        hana_service = env.get_service(label='hana')
        
        if not hana_service:
            raise RuntimeError("HANA service not found in VCAP_SERVICES")
        
        creds = hana_service.credentials
        
        # Extract connection parameters
        host = creds.get('host')
        port = int(creds.get('port'))
        
        # Use runtime user for connection
        # HDI user is for deployment/migration, runtime user is for application
        user = creds.get('user')
        password = creds.get('password')
        
        # Get certificate for SSL connection
        certificate = creds.get('certificate')
        database_id = creds.get('database_id')
        
        # Get schema from binding (important for HDI containers)
        schema_name = creds.get('schema')
        
        logger.info(
            "VCAP config loaded: host=%s, port=%s, database_id=%s, schema=%s",
            host, port, database_id, schema_name
        )

        validate_certificate = self._resolve_validate_certificate()
        
        # Create configuration with SSL settings for cloud deployment
        self._config = ConnectionConfig(
            host=host,
            port=port,
            user=user,
            password=password,
            schema_name=schema_name,
            certificate=certificate,
            database_id=database_id,
            encrypt=True,  # Always encrypt for cloud connections
            validate_certificate=validate_certificate,
        )

        logger.info(
            "VCAP SSL validation mode resolved: validate_certificate=%s",
            validate_certificate,
        )
        
        return self._config
    
    def get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Creates SSL context for secure connection to SAP HANA Cloud.
        
        Returns:
            ssl.SSLContext: Configured SSL context with certificate
        """
        config = self.get_connection_config()
        
        if not config.certificate:
            logger.warning("No certificate found in VCAP_SERVICES")
            return None
        
        logger.info("Creating SSL context for HANA Cloud connection")

        # Normalize certificate string for common payload formats.
        certificate = config.certificate.strip()
        if "\\n" in certificate:
            certificate = certificate.replace("\\n", "\n")

        # Create SSL context with certificate from VCAP when it is usable.
        ssl_context = ssl.create_default_context()
        if "-----BEGIN CERTIFICATE-----" in certificate:
            try:
                ssl_context.load_verify_locations(cadata=certificate)
            except ssl.SSLError as exc:
                logger.warning(
                    "Failed to load certificate from VCAP_SERVICES; "
                    "falling back to system CA trust store: %s",
                    exc,
                )
        else:
            logger.warning(
                "VCAP certificate payload does not contain PEM header; "
                "using system CA trust store"
            )
        
        if not config.validate_certificate:
            # Allow explicit local debug override to skip hostname and CA validation.
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        
        logger.info(
            "SSL context created: validate_certificate=%s",
            config.validate_certificate
        )
        
        return ssl_context
    
    def create_engine(self, min_connections: int, max_connections: int, database_name: Optional[str] = None):
        """
        Create SQLAlchemy engine for VCAP mode using custom creator.
        This is the VCAP-specific way of creating engine with SSL support.
        
        Args:
            min_connections: Minimum pool size
            max_connections: Maximum pool size
            database_name: Optional database name (not used in VCAP mode)
            
        Returns:
            Engine: SQLAlchemy engine configured with SSL creator
        """
        from sqlalchemy import create_engine
        from hdbcli import dbapi
        
        # Get connection config
        conn_config = self.get_connection_config()
        
        logger.info(
            "VCAP mode: Creating engine with SSL - host=%s, port=%s, schema=%s, has_cert=%s",
            conn_config.host, conn_config.port, conn_config.schema_name,
            bool(conn_config.certificate)
        )
        
        # Define custom creator function for VCAP mode
        def create_vcap_connection():
            """Custom connection creator with SSL for VCAP mode"""
            # Get SSL context
            ssl_context = self.get_ssl_context()
            
            # Build connection parameters
            conn_params = {
                'address': conn_config.host,
                'port': conn_config.port,
                'user': conn_config.user,
                'password': conn_config.password,
                'encrypt': conn_config.encrypt,
            }
            
            # Add schema if available
            if conn_config.schema_name:
                conn_params['currentSchema'] = conn_config.schema_name
            
            # Add SSL context if configured
            if ssl_context:
                conn_params['sslContext'] = ssl_context
            
            # Always pass certificate validation mode for encrypted VCAP connections.
            if conn_config.encrypt:
                conn_params['sslValidateCertificate'] = conn_config.validate_certificate
            
            try:
                conn = dbapi.connect(**conn_params)
                logger.info("VCAP mode: HANA connection established successfully")
                return conn
            except Exception as e:
                logger.error("VCAP mode: Failed to connect to HANA: %s", str(e))
                logger.error(
                    "Connection details: host=%s, port=%s, user=%s..., encrypt=%s",
                    conn_config.host, conn_config.port, conn_config.user[:20], conn_config.encrypt
                )
                raise
        
        # Create engine with custom creator
        engine = create_engine(
            "hana+hdbcli://",
            creator=create_vcap_connection,
            pool_size=min_connections,
            max_overflow=max_connections - min_connections,
            pool_pre_ping=True,
        )
        
        logger.info("VCAP mode: Engine created successfully")
        return engine
