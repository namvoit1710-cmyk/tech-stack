import logging

from agent_sdk import tool

from app.layer2_application.features.transform_column import transform_column as _impl
from app.layer2_application.interfaces.data_factory_gateway import IDataFactoryGateway
from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway
from app.layer2_application.interfaces.settings_provider import IRuntimeSettingsProvider
from app.layer2_application.interfaces.structured_llm_gateway import (
    IStructuredLLMGateway,
)
from app.layer4_frameworks.dependency.runtime_dependency_refs import (
    RuntimeDependencyRef,
)

logger = logging.getLogger(__name__)


def make_transform_column_tool(
    *,
    data_factory_gateway_ref: RuntimeDependencyRef[IDataFactoryGateway],
    structured_llm_gateway_ref: RuntimeDependencyRef[IStructuredLLMGateway],
    file_preview_gateway: IFilePreviewGateway,
    settings_provider: IRuntimeSettingsProvider,
):
    @tool(internal=True)
    async def transform_column(
        long_input: str | None = None, file_id: str | None = None
    ) -> dict:
        """
        Transform columns in a file using natural language instructions.

        Capabilities:
        - Filter columns using conditions
        - Create, insert, or update columns using expressions
        - Drop columns
        - Rename columns
        - Assign values using conditional logic

        Args:
            long_input: Natural-language column transformation instruction.
            file_id: Input file identifier.

        Batching:
            Supports multiple column operations in a single call.

        Output behavior:
            Always produces a NEW file. The returned file_id represents the
            latest version and must be used for subsequent chained steps.

        Returns:
            {
                "file_id": str,
                "download_url": str,
                "message": str
            }
        """
        logger.info("[HYBRID-TOOL] transform_column called (uses LLM + MCP)")
        return await _impl(
            long_input=long_input,
            file_id=file_id,
            structured_llm_gateway=structured_llm_gateway_ref.require(),
            data_factory_gateway=data_factory_gateway_ref.require(),
            file_preview_gateway=file_preview_gateway,
            settings_provider=settings_provider,
        )

    return transform_column


__all__ = ["make_transform_column_tool"]
