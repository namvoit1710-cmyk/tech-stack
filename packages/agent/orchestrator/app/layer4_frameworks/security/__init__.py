"""Security infrastructure factory."""

from __future__ import annotations

import logging

from app.layer2_application.interfaces.permission_provider import IPermissionProvider

logger = logging.getLogger(__name__)


def create_permission_provider(settings) -> IPermissionProvider:
    """Factory: build the permission provider."""
    profile_api_base_url = getattr(settings, "PROFILE_API_BASE_URL", "")

    if profile_api_base_url:
        from app.layer4_frameworks.security.user_permission_provider import UserPermissionProvider

        provider = UserPermissionProvider(base_url=profile_api_base_url)
        logger.info("Permission provider: user-permission (%s)", profile_api_base_url)
        return provider

    from app.layer4_frameworks.security.mock_permission_provider import MockPermissionProvider

    provider = MockPermissionProvider()
    logger.info("Permission provider: mock")
    return provider
