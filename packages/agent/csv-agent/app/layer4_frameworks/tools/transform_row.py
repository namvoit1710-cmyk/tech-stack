from agent_sdk import tool

from app.layer2_application.features.transform_row import transform_row as _impl
from app.layer2_application.interfaces.data_factory_gateway import IDataFactoryGateway
from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider
from app.layer2_application.interfaces.structured_llm_gateway import (
    IStructuredLLMGateway,
)
from app.layer4_frameworks.dependency.runtime_dependency_refs import (
    RuntimeDependencyRef,
)


def make_transform_row_tool(
    *,
    data_factory_gateway_ref: RuntimeDependencyRef[IDataFactoryGateway],
    structured_llm_gateway_ref: RuntimeDependencyRef[IStructuredLLMGateway],
    file_preview_gateway: IFilePreviewGateway,
    settings_provider: IRuntimeSettingsProvider,
):
    @tool(internal=True)
    async def transform_row(
        long_input: str | None = None, file_id: str | None = None
    ) -> dict:
        """
        Transform rows in a file using natural language instructions.

        Capabilities:
        - Delete rows by index, condition, or exact values
        - Insert rows at the start of the CSV file or at a specific index

        Args:
            long_input: Natural-language row transformation instruction.
            file_id: Input file identifier.

        Batching:
            Supports multiple row operations in a single call.

        Output behavior:
            Always creates a NEW file after transformations. The returned file_id
            represents the latest version and must be used for chained steps.

        Returns:
            {
                "file_id": str,
                "download_url": str,
                "message": str
            }
        """
        return await _impl(
            long_input=long_input,
            file_id=file_id,
            structured_llm_gateway=structured_llm_gateway_ref.require(),
            data_factory_gateway=data_factory_gateway_ref.require(),
            file_preview_gateway=file_preview_gateway,
            settings_provider=settings_provider,
        )

    return transform_row


__all__ = ["make_transform_row_tool"]
