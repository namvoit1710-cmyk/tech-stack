from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Governance Smart API"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
