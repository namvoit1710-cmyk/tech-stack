"""Configuration management using Pydantic Settings."""

from urllib.parse import quote_plus

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class DatabaseSettings(BaseModel):
    """Database connection settings."""

    database_host: str
    database_port: int
    database_user: str
    database_password: SecretStr = Field(..., repr=False)  # Fix the import for SecretStr
    database_name: str
    database_echo: bool
    database_pool_size: int
    database_max_overflow: int
    database_pool_timeout: int
    database_schema: str

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="Agent Registry Service", alias="APP_NAME")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=False, alias="DEBUG")
    port: int = Field(default=8000, alias="APP_PORT")
    
    # SA-1938: model / provider / temperature carry NO fallback. They are chosen by the
    # caller and stored verbatim; omitted means NULL, never an invented value. The
    # DEFAULT_MODEL / DEFAULT_PROVIDER / DEFAULT_TEMPERATURE env vars are therefore no
    # longer read (deployment.yml may still declare them; extra="ignore" drops them).
    # The remaining defaults below are NOT declared in deployment.yml, so they keep a
    # code-level default or the service would fail to boot.
    default_max_tokens: int = Field(default=1024, alias="DEFAULT_MAX_TOKENS")
    default_timeout_ms: int = Field(default=30000, alias="DEFAULT_TIMEOUT_MS")
    default_max_concurrency: int = Field(default=1, alias="DEFAULT_MAX_CONCURRENCY")
    default_retry_count: int = Field(default=3, alias="DEFAULT_RETRY_COUNT")
    default_streaming_supported: bool = Field(default=False, alias="DEFAULT_STREAMING_SUPPORTED")
    
    # External API
    fetch_workflow_url: str = Field(..., alias="FETCH_WORKFLOW_URL")
    fetch_current_user_info_url: str = Field(..., alias="FETCH_CURRENT_USER_INFO_URL")
    fetch_current_user_groups_url: str = Field(..., alias="FETCH_CURRENT_USER_GROUPS_URL")
    fetch_current_user_groups_details_url: str = Field(..., alias="FETCH_CURRENT_USER_GROUPS_DETAILS_URL")
    # PM SCIM get-user-by-id (RBAC Task 3 assign-role). Format placeholder: {external_id}.
    # Optional (default empty) so the app boots without it; assign-role degrades to an
    # external_id-only upsert when it is unset/unreachable.
    fetch_user_by_id_url: str = Field(default="", alias="FETCH_USER_BY_ID_URL")
    fetch_timeout_seconds: int = Field(default=30, alias="FETCH_TIMEOUT_SECONDS")
    fetch_retry_count: int = Field(default=3, alias="FETCH_RETRY_COUNT")  # total attempts (retries = n-1); 1 = no retry
    fetch_backoff_seconds: int = Field(default=2, alias="FETCH_BACKOFF_SECONDS")
    fetch_max_backoff_seconds: int = Field(default=12, alias="FETCH_MAX_BACKOFF_SECONDS")
    fetch_min_backoff_seconds: int = Field(default=1, alias="FETCH_MIN_BACKOFF_SECONDS")
    retryable_exceptions: list[int] = Field(
        default=[429, 500, 502, 503, 504], alias="RETRYABLE_EXCEPTIONS"
    )
    
    # Database (SAP HANA)
    database_host: str = Field(default="localhost", alias="DATABASE_HOST")
    database_port: int = Field(default=30015, alias="DATABASE_PORT")
    database_name: str = Field(default="", alias="DATABASE_NAME")
    database_user: str = Field(default="SYSTEM", alias="DATABASE_USER")
    database_password: SecretStr = Field(default="", alias="DATABASE_PASSWORD")
    database_schema: str = Field(default="SIMPLEMDG_AGENT_REGISTRY_DB", alias="DATABASE_SCHEMA")
    database_pool_size: int = Field(default=10, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=20, alias="DATABASE_MAX_OVERFLOW")
    database_pool_timeout: int = Field(default=30, alias="DATABASE_POOL_TIMEOUT")
    database_echo: bool = Field(default=False, alias="DATABASE_ECHO")
    # Auto-create tables on startup (D26). OFF by default: every real environment is
    # provisioned by `alembic upgrade` — running `create_all()` on an alembic-migrated
    # HANA schema misfires (its schema-scoped existence check doesn't see the tables and
    # re-issues `CREATE TABLE agents` → HANA "duplicate table name"). Set DB_AUTO_CREATE=true
    # ONLY for an ephemeral/fresh DB with no migrations (e.g. a throwaway local run).
    db_auto_create: bool = Field(default=False, alias="DB_AUTO_CREATE")

    # Health Check
    health_check_interval_seconds: int = Field(
        default=15, alias="HEALTH_CHECK_INTERVAL_SECONDS"
    )
    health_check_timeout_seconds: int = Field(
        default=5, alias="HEALTH_CHECK_TIMEOUT_SECONDS"
    )
    health_check_retry_count: int = Field(default=2, alias="HEALTH_CHECK_RETRY_COUNT")  # total attempts (retries = n-1); 1 = no retry
    health_check_concurrent_size: int = Field(
        default=10, alias="HEALTH_CHECK_CONCURRENT_SIZE"
    )
    health_check_backoff_seconds: int = Field(
        default=2, alias="HEALTH_CHECK_BACKOFF_SECONDS"
    )

    # RBAC (SA RBAC v1)
    # Phase 1 fail-open (B1): a token-less internal call resolves to the `system`
    # principal (bypasses permission checks) because all external traffic is
    # authenticated at the gateway. Phase 2: give internal services a dedicated token
    # (resolved like any user token) and flip this OFF so no-token → 401.
    trust_unauthenticated_internal: bool = Field(
        default=True, alias="TRUST_UNAUTHENTICATED_INTERNAL"
    )

    # API
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")
    api_cors_origins: list[str] = Field(
        default=["*"], alias="API_CORS_ORIGINS"
    )  # Set explicitly in production
    api_rate_limit: int = Field(default=100, alias="API_RATE_LIMIT")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")  # json or console

    @property
    def database_url(self) -> str:
        """Build database connection URL for SAP HANA with synchronous hdbcli driver.
        
        URL-encodes password to handle special characters like @ # etc.
        Uses hana+hdbcli:// dialect for synchronous operations.
        URL-encode password to handle special characters
        """
        encoded_password = quote_plus(self.database_password.get_secret_value())
        
        return (
            f"hana+hdbcli://{self.database_user}:{encoded_password}@"
            f"{self.database_host}:{self.database_port}?databaseName={self.database_name}"
        )
        
    def get_database_settings(self) -> DatabaseSettings:
        """Get database settings as a DatabaseSettings instance."""
        return DatabaseSettings(
            database_host=self.database_host,
            database_port=self.database_port,
            database_user=self.database_user,
            database_password=self.database_password.get_secret_value(),
            database_name=self.database_name,
            database_echo=self.database_echo,
            database_pool_size=self.database_pool_size,
            database_max_overflow=self.database_max_overflow,
            database_pool_timeout=self.database_pool_timeout,
            database_schema=self.database_schema,
        )