from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    APP_NAME: str = "Wait worker"
    WORKER_TYPE: str = "wait"
    SERVER_PORT: int = 35000


settings = Settings()
