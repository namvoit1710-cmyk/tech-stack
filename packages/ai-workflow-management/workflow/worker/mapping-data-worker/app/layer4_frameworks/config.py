from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    APP_NAME: str = "Mapping Data Worker"
    WORKER_NAME: str = "Mapping Data Worker"
    WORKER_TYPE: str = "mapping_data"
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "ArrowLeftRight"
    WORKER_COLOR: str = "#F59E0B"
    WORKER_TAGS: str = "mapping,transform,data,technical"


settings = Settings()
