from datetime import timedelta

from fastapi import HTTPException

import litellm
from litellm.proxy._types import CommonProxyErrors, InvitationNew, UserAPIKeyAuth
from litellm.proxy.management_helpers.invitation_email_guard import (
    is_email_missing_for_invitation,
)
from litellm.repositories.table_repositories import InvitationLinkRepository
from litellm.repositories.user_repository import UserRepository


async def create_invitation_for_user(
    data: InvitationNew,
    user_api_key_dict: UserAPIKeyAuth,
):
    """
    Create an invitation for the user to onboard to LiteLLM Admin UI.

    Refuses when the target user has no `user_email`: login via an invite
    link looks the user up by email (see `authenticate_user`), so an
    invitation for an emailless user creates a password nobody can ever use
    to log in.
    """
    from litellm.proxy.proxy_server import litellm_proxy_admin_name, prisma_client

    if prisma_client is None:
        raise HTTPException(
            status_code=400,
            detail={"error": CommonProxyErrors.db_not_connected_error.value},
        )

    user_row = await UserRepository(prisma_client).find_by_id(data.user_id)
    if user_row is None or is_email_missing_for_invitation(user_row.user_email):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Cannot create an invitation for a user with no email set. "
                "Set `user_email` on the user before inviting them."
            },
        )

    current_time = litellm.utils.get_utc_datetime()
    expires_at = current_time + timedelta(days=7)

    try:
        response = await InvitationLinkRepository(prisma_client).table.create(
            data={
                "user_id": data.user_id,
                "created_at": current_time,
                "expires_at": expires_at,
                "created_by": user_api_key_dict.user_id or litellm_proxy_admin_name,
                "updated_at": current_time,
                "updated_by": user_api_key_dict.user_id or litellm_proxy_admin_name,
            }  # type: ignore
        )
        return response
    except Exception as e:
        if "Foreign key constraint failed on the field" in str(e):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "User id does not exist in 'LiteLLM_UserTable'. Fix this by creating user via `/user/new`."
                },
            )
        raise HTTPException(status_code=500, detail={"error": str(e)})
