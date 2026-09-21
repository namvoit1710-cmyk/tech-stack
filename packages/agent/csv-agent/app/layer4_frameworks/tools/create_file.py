from agent_sdk import ILLMService, tool

from app.layer2_application.features.create_file import create_file_service
from app.layer2_application.interfaces.file_upload_gateway import IFileUploadGateway
from app.layer4_frameworks.dependency.runtime_dependency_refs import (
    RuntimeDependencyRef,
)


class _LLMAdapterBridge:
    """Compatibility bridge for create_file_service's existing llm_adapter contract."""

    def __init__(self, llm_service_ref: RuntimeDependencyRef[ILLMService]):
        self._llm_service_ref = llm_service_ref

    async def generate_completion(self, system_prompt: str, user_prompt: str) -> str:
        return await self._llm_service_ref.require().get_chat_completion(
            system_prompt, user_prompt
        )


def make_create_file_tool(
    *,
    llm_service_ref: RuntimeDependencyRef[ILLMService],
    file_upload_gateway: IFileUploadGateway,
):
    @tool(internal=True)
    async def create_file(filename: str, description: str) -> dict:
        """
        Create a CSV file based on user requirements using AI.

        This tool uses an LLM to generate CSV content from a natural-language
        description, then uploads the generated file to the file service.

        Use this when the user needs to:
        - Generate sample or test data matching a description
        - Create CSV files from natural-language requirements
        - Quickly prototype data structures

        Args:
            filename: Name of the CSV file. The service may add .csv when needed.
            description: Natural-language description of what the CSV should contain.

        Returns:
            {
                "message": str,
                "file_id": str,
                "filename": str,
                "rows_count": int
            }
        """
        llm_adapter = _LLMAdapterBridge(llm_service_ref)
        return await create_file_service(
            filename=filename,
            description=description,
            llm_adapter=llm_adapter,
            file_upload_adapter=file_upload_gateway,
        )

    return create_file


__all__ = ["make_create_file_tool"]
