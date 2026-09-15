"""Configuration for Database Connection Worker."""

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """Database Connection Worker settings."""
    
    APP_NAME: str = "Database Connection Worker"
    WORKER_NAME: str = "Database Connection Worker"
    WORKER_TYPE: str = "database_connection"
    SERVER_PORT: int = 37000
    
    # Database worker specific settings
    # DEFAULT_MAX_ROWS: int = 1000
    # DEFAULT_TIMEOUT_SECONDS: int = 30
    # DEFAULT_POOL_SIZE: int = 5
    # DEFAULT_POOL_TIMEOUT: int = 30


settings = Settings()
