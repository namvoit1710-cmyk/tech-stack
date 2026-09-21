from __future__ import annotations

from typing import Any

from agent_sdk.layer1_domain.entities.agent_info import AgentInfo
from agent_sdk.layer2_application.interfaces.observability import ILogger


def _normalize_capabilities(settings: Any) -> list[dict[str, Any]]:
    capabilities = []
    for capability in list(settings.CAPABILITIES or []):
        if isinstance(capability, dict):
            capabilities.append(capability)
            continue
        if isinstance(capability, str):
            capabilities.append({"domain": settings.AGENT_DOMAIN, "action": capability})
            continue
        capabilities.append(
            {"domain": settings.AGENT_DOMAIN, "action": str(capability)}
        )
    return capabilities


class GetAgentInfoUseCase:
    def __init__(
        self,
        logger: ILogger,
        settings: Any,
        **kwargs,
    ) -> None:
        self.logger = logger
        self._settings = settings

    def execute(self) -> AgentInfo:
        self.logger.info("Retrieving agent info")
        capabilities = _normalize_capabilities(self._settings)
        default_tenant_id = getattr(self._settings, "DEFAULT_TENANT_ID", "default")
        metadata = {
            "tenant_aware": True,
            "default_tenant_id": default_tenant_id,
        }
        return AgentInfo(
            agent_type=self._settings.AGENT_TYPE,
            version=self._settings.AGENT_VERSION,
            sdk_version=self._settings.SDK_VERSION,
            domain=self._settings.AGENT_DOMAIN,
            capabilities=capabilities,
            metadata=metadata,
        )
