from agent_sdk import tool

from app.layer2_application.features.get_first_n_rows import get_first_n_rows_impl
from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway


def make_get_first_n_rows_tool(*, file_preview_gateway: IFilePreviewGateway):
    @tool(internal=True)
    def get_first_n_rows(file_id: str, num_rows: int) -> dict:
        """
        Return the first n rows in a file.

        Args:
            file_id: The ID of the file.
            num_rows: The number of rows to retrieve.

        Returns:
            A dictionary containing the file ID and the preview rows.
        """
        return get_first_n_rows_impl(
            file_id,
            num_rows,
            file_preview_gateway=file_preview_gateway,
        )

    return get_first_n_rows


__all__ = ["make_get_first_n_rows_tool"]
