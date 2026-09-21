#### Analytics Endpoints #####
import os

from fastapi import APIRouter

from litellm.types.proxy.discovery_endpoints.ui_discovery_endpoints import (
    UiDiscoveryEndpoints,
)

router = APIRouter()


def _is_email_logger_configured() -> bool:
    """True when an email-invitation logger callback is actually registered.

    Lets the UI stop offering `send_invite_email` when nothing can send it,
    instead of ticking the box by default and silently failing. See
    litellm/litellm_core_utils/logging_callback_manager.py:callback_is_active.
    """
    import litellm

    try:
        from litellm_enterprise.enterprise_callbacks.send_emails.base_email import (
            BaseEmailLogger,
        )
    except ImportError:
        return False

    return litellm.logging_callback_manager.callback_is_active(BaseEmailLogger)


@router.get("/.well-known/litellm-ui-config", response_model=UiDiscoveryEndpoints)
@router.get("/litellm/.well-known/litellm-ui-config", response_model=UiDiscoveryEndpoints)  # if mounted at root path
async def get_ui_config():
    from litellm.proxy.auth.auth_utils import _has_user_setup_sso
    from litellm.proxy.utils import get_proxy_base_url, get_server_root_path

    from litellm.proxy.proxy_server import general_settings

    auto_redirect_ui_login_to_sso = (
        os.getenv("AUTO_REDIRECT_UI_LOGIN_TO_SSO", "false").lower() == "true"
        or general_settings.get("auto_redirect_ui_login_to_sso", False) is True
    )
    admin_ui_disabled = os.getenv("DISABLE_ADMIN_UI", "false").lower() == "true"
    hide_default_credentials_hint = bool(
        os.getenv("LITELLM_HIDE_DEFAULT_CREDENTIALS_HINT", "false").lower() == "true"
        or general_settings.get("hide_default_credentials_hint", False) is True
    )

    sso_configured = _has_user_setup_sso()

    from litellm.proxy.proxy_server import proxy_config

    is_control_plane = len(proxy_config.worker_registry) > 0

    return UiDiscoveryEndpoints(
        server_root_path=get_server_root_path(),
        proxy_base_url=get_proxy_base_url(),
        auto_redirect_to_sso=sso_configured and auto_redirect_ui_login_to_sso,
        admin_ui_disabled=admin_ui_disabled,
        sso_configured=sso_configured,
        hide_default_credentials_hint=hide_default_credentials_hint,
        is_control_plane=is_control_plane,
        workers=proxy_config.worker_registry if is_control_plane else [],
        email_logger_configured=_is_email_logger_configured(),
    )
