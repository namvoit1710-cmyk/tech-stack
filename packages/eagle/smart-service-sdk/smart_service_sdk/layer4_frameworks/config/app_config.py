from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "AI Eagle"
    APP_VERSION: str = "0.1.0"
    APP_MODE: str = "REST"
    ENABLE_RESUME_RUNNING_BACKGROUND_JOBS: bool = True

    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"

    HANA_HOST: str = "localhost"
    HANA_PORT: int = 443
    HANA_USER: str = "SYSTEM"
    HANA_PASSWORD: str = ""
    HANA_SCHEMA: str = ""
    HANA_POOL_SIZE: int = 5
    HANA_POOL_TIMEOUT_SECONDS: float = 30
    HANA_STATEMENT_TIMEOUT_MS: int = 30000
    AUTO_CREATE_SCHEMA: bool = False
    AUTO_SEED_DATA: bool = False

    EMBEDDING_MODEL_NAME: str = "microsoft/harrier-oss-v1-270m"
    EMBEDDING_VECTOR_DIMENSIONS: int = 640
    EMBEDDING_QUERY_INSTRUCTION: str = (
        "Represent this query for retrieving relevant documents:"
    )
    EMBEDDING_BATCH_SIZE: int = 32
    INGESTION_MAX_EMBEDDING_INPUT_CHARACTERS: int = 4000
    INGESTION_DEFAULT_CHUNK_SIZE_CHARACTERS: int = 800

    LOCAL_FILE_STORAGE_PATH: str = "./local-files"
    LOCAL_UPLOAD_MAX_BYTES: int = 100 * 1024 * 1024

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4.1-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_TIMEOUT_SECONDS: float = 30.0
    OPENAI_MAX_RETRIES: int = 2
    OPENAI_RETRY_BACKOFF_SECONDS: float = 1.0

    FILE_SERVICE_BASE_URL: str = ""
    FILE_SERVICE_TIMEOUT_SECONDS: float = 60.0

    GRAPH_WORKSPACE_NAME: str = "RAG_GRAPH_WORKSPACE"
    SPACY_MODEL_NAME: str = "en_core_web_sm"
    GRAPH_SEED_TOP_K: int = 8
    GRAPH_NEIGHBOR_CAP_PER_SEED: int = 6
    GRAPH_MAX_RELATION_CANDIDATES: int = 24
    GRAPH_MAX_GRAPH_CHUNK_CANDIDATES: int = 16

    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RESET_SECONDS: float = 60.0

    DEFAULT_TENANT_ID: str = "default-tenant"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
