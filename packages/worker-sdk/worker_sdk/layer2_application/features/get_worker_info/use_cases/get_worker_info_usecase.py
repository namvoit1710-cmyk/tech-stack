from dataclasses import dataclass, field
from typing import Any, Optional

from worker_sdk.layer2_application.interfaces.app_config_interface import IAppConfig
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer1_domain.value_objects.port import default_task_ports


@dataclass
class GetWorkerInfoCommand:
    pass


@dataclass
class GetWorkerInfoResult:
    worker_type: str
    version: str
    sdk_version: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    input_schema: Optional[Any] = None
    output_schema: Optional[Any] = None
    name: str = ""
    description: str = ""
    node_class: str = "BUSINESS"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    capabilities: list[dict[str, str]] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=default_task_ports)


class GetWorkerInfoUseCase:
    def __init__(self, logger: ILogger, input_schema: Optional[Any] = None,
                 output_schema: Optional[Any] = None,
                 capabilities: Optional[list[dict[str, str]]] = None,
                 ports: Optional[dict[str, list[dict[str, Any]]]] = None,
                 app_config: IAppConfig | None = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self._app_config = app_config
        self.input_schema = input_schema
        self.output_schema = output_schema
        worker_type = getattr(app_config, "WORKER_TYPE", "generic")
        self.capabilities = capabilities or [{"domain": worker_type, "action": "execute"}]
        self.ports = ports or default_task_ports()

    def execute(self, request: GetWorkerInfoCommand) -> GetWorkerInfoResult:
        self.logger.info("Returning worker info")
        cfg = self._app_config
        worker_type = getattr(cfg, "WORKER_TYPE", "generic")
        worker_name = getattr(cfg, "WORKER_NAME", "")
        return GetWorkerInfoResult(
            worker_type=worker_type,
            version=getattr(cfg, "WORKER_VERSION", "0.1.0"),
            sdk_version=getattr(cfg, "SDK_VERSION", "1.0.0"),
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            name=worker_name or worker_type,
            description=getattr(cfg, "WORKER_DESCRIPTION", ""),
            node_class=getattr(cfg, "WORKER_NODE_CLASS", "TECHNICAL"),
            icon=getattr(cfg, "WORKER_ICON", "Cog"),
            color=getattr(cfg, "WORKER_COLOR", "#3B82F6"),
            tags=cfg.get_tags_list() if cfg is not None else [],
            capabilities=self.capabilities,
            ports=self.ports,
        )
