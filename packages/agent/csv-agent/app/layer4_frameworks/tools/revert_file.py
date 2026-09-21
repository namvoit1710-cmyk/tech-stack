import logging

from agent_sdk import ILLMService, tool

from app.layer2_application.features.revert_file import revert_file as _impl
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider
from app.layer4_frameworks.dependency.runtime_dependency_refs import (
    RuntimeDependencyRef,
)

logger = logging.getLogger(__name__)


def make_revert_file_tool(
    *,
    llm_service_ref: RuntimeDependencyRef[ILLMService],
    settings_provider: IRuntimeSettingsProvider,
):
    @tool(internal=True)
    async def revert_file(
        revert_instruction: str = "", conversation_history: str = ""
    ) -> dict:
        """
        Revert to an older file version using a user request and conversation history.

        Args:
            revert_instruction: User request describing which older version to restore.
            conversation_history: Rendered chronological conversation history string.

        Returns:
            {
                "file_id": str,
                "download_url": str,
                "message": str
            }
        """
        logger.info("[TOOL] revert_file called")
        return await _impl(
            revert_instruction=revert_instruction,
            conversation_history=conversation_history,
            llm_service=llm_service_ref.require(),
            settings_provider=settings_provider,
        )

    return revert_file


__all__ = ["make_revert_file_tool"]
