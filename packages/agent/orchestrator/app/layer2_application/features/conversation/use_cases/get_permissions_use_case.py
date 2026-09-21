from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from app.layer2_application.interfaces.permission_provider import IPermissionProvider
from app.layer1_domain.entities.permission.permission_collector import PermissionCollector


@dataclass
class GetPermissionsInput:
    user_id: str = "anonymous"


@dataclass
class GetPermissionsOutput:
    user_id: str = ""
    roles: List[str] = field(default_factory=list)
    allowed_agents: List[str] = field(default_factory=list)
    subscription_tier: str = ""
    resource_quotas: Dict[str, Any] = field(default_factory=dict)


class GetPermissionsUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        permission_provider: IPermissionProvider,
    ):
        self.logger = logger
        self.monitor = monitor
        self.permission_provider = permission_provider

    def execute(self, request: GetPermissionsInput) -> GetPermissionsOutput:
        if self.permission_provider is None:
            raise RuntimeError("permission_provider is required but was not provided")
        raw_perms = self.permission_provider.get_user_permissions(request.user_id)
        context = PermissionCollector.collect(request.user_id, raw_perms)
        return GetPermissionsOutput(
            user_id=context.user_id,
            roles=list(context.roles),
            allowed_agents=list(context.allowed_agents),
            subscription_tier=context.subscription_tier,
            resource_quotas=context.resource_quotas,
        )
