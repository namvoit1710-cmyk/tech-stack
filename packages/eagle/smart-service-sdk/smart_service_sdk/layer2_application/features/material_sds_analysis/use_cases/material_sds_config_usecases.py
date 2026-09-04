from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    MaterialSdsSourceConfig,
    RuntimeConfigurationService,
)

# Bound the list sizes so a config update cannot grow unbounded (SA-1474 AC2).
MAX_RESOURCE_URLS = 50
MAX_ALLOWED_DOMAINS = 50

# A pragmatic hostname check: labels of letters/digits/hyphens, at least one dot,
# and a 2+ char alphabetic TLD. Rejects schemes, paths, ports, and whitespace.
_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}$"
)


class MaterialSdsSourceConfigValidationError(ValueError):
    """Raised when a submitted SDS source config fails validation."""


@dataclass(frozen=True)
class UpdateMaterialSdsSourceConfigCommand:
    resource_urls: list[str]
    allowed_domains: list[str]


class GetMaterialSdsSourceConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(self) -> MaterialSdsSourceConfig:
        return await self._runtime_configuration_service.get_material_sds_source_config()


class UpdateMaterialSdsSourceConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(
        self, command: UpdateMaterialSdsSourceConfigCommand
    ) -> MaterialSdsSourceConfig:
        resource_urls = _validate_resource_urls(command.resource_urls)
        allowed_domains = _validate_allowed_domains(command.allowed_domains)
        return await self._runtime_configuration_service.update_material_sds_source_config(
            MaterialSdsSourceConfig(
                resource_urls=resource_urls,
                allowed_domains=allowed_domains,
            )
        )


def _validate_resource_urls(raw_urls: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in raw_urls:
        url = str(candidate).strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise MaterialSdsSourceConfigValidationError(
                f"Resource URL must use https: {url!r}"
            )
        if not parsed.netloc:
            raise MaterialSdsSourceConfigValidationError(
                f"Resource URL is malformed (no host): {url!r}"
            )
        if url not in seen:
            seen.add(url)
            cleaned.append(url)
    if len(cleaned) > MAX_RESOURCE_URLS:
        raise MaterialSdsSourceConfigValidationError(
            f"Too many resource URLs (max {MAX_RESOURCE_URLS})."
        )
    return cleaned


def _validate_allowed_domains(raw_domains: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in raw_domains:
        domain = str(candidate).strip().lower()
        if not domain:
            continue
        if "://" in domain or "/" in domain or " " in domain:
            raise MaterialSdsSourceConfigValidationError(
                f"Allowed domain must be a bare host, not a URL: {candidate!r}"
            )
        if not _DOMAIN_PATTERN.match(domain):
            raise MaterialSdsSourceConfigValidationError(
                f"Allowed domain is malformed: {candidate!r}"
            )
        if domain not in seen:
            seen.add(domain)
            cleaned.append(domain)
    if len(cleaned) > MAX_ALLOWED_DOMAINS:
        raise MaterialSdsSourceConfigValidationError(
            f"Too many allowed domains (max {MAX_ALLOWED_DOMAINS})."
        )
    return cleaned
