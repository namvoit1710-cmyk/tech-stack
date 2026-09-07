import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Clean Data Factory"
    APP_MODE: str = "REST"
    PORT: int = 8000
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"
    DB_ECHO: bool = False
    FILE_SERVER_URL: str = "http://localhost:5000"
    FILE_SERVICE_CONNECT_TIMEOUT_SEC: int = 10
    FILE_SERVICE_READ_TIMEOUT_SEC: int = 180
    FILE_SERVICE_UPLOAD_TIMEOUT_SEC: int = 300
    FILE_SERVICE_UPLOAD_MODE: str = "multipart"
    FILE_SERVICE_MAX_RETRIES: int = 3
    FILE_SERVICE_RETRY_BACKOFF_SEC: float = 1.5
    FILE_SERVICE_FAIL_FAST_ON_TIMEOUT: bool = True
    FILE_SERVICE_USE_PRESIGNED_DOWNLOAD_FOR_LARGE: bool = True
    FILE_SERVICE_PRESIGNED_DOWNLOAD_MIN_SIZE_MB: int = 25
    FILE_SERVICE_PRESIGNED_DOWNLOAD_EXPIRES_SEC: int = 3600
    FILE_SERVICE_USE_PRESIGNED_UPLOAD_FOR_LARGE: bool = True
    FILE_SERVICE_PRESIGNED_UPLOAD_MIN_SIZE_MB: int = 100
    ROW_TRANSFORM_PREVIEW_MAX_ROWS: int = 10
    ROW_TRANSFORM_PREVIEW_MAX_COLUMNS: int = 60
    MEMORY_GUARD_ENABLED: bool = True
    MEMORY_GUARD_MIN_AVAILABLE_PERCENT: float = 5.0
    MEMORY_GUARD_MIN_AVAILABLE_MB: int = 512
    ADAPTIVE_BATCHING_ENABLED: bool = True
    ADAPTIVE_BATCH_RUNTIME_SHRINK_ENABLED: bool = True
    ADAPTIVE_BATCH_ALLOW_GROWTH: bool = False
    ADAPTIVE_BATCH_REEVALUATE_EVERY_N_BATCHES: int = 1
    TRANSFORM_LAZY_CSV_FAST_PATH_ENABLED: bool = False
    TRANSFORM_LAZY_CSV_SINK_ENABLED: bool = True
    BATCH_SIZE: int = 500000
    BATCH_SIZE_MIN: int = 100000
    BATCH_SIZE_MAX: int = 2000000
    BATCH_MEMORY_FRACTION: float = 0.1
    BATCH_WORKING_SET_MULTIPLIER: float = 5.0
    BATCH_FALLBACK_BYTES_PER_ROW: int = 256
    BATCH_FALLBACK_BYTES_PER_COLUMN: int = 64
    MAX_FILE_SIZE: int = 100
    # Comma-separated list of allowed CORS origins. Empty means no cross-origin
    # access is permitted (secure default; never use "*").
    CORS_ALLOW_ORIGINS: str = ""
    # Toggle for verbose performance profiling; read here instead of scattered os.environ calls.
    PERFORMANCE_PROFILING_ENABLED: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_allow_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]


settings = Settings()
