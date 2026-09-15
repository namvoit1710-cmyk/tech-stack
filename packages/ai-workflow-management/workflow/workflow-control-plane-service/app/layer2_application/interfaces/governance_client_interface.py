"""Interface for the Governance API client."""
from abc import abstractmethod
from typing import Any, Protocol

from app.layer1_domain.template_schema.models import TemplateFieldConfig


class IGovernanceClient(Protocol):
    """Protocol for fetching change request approval data from Governance service."""

    @abstractmethod
    async def get_approval_process(self, cr_id: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch approval process steps for a change request.

        Args:
            cr_id: The change request ID.

        Returns:
            Parsed governance payload.
            Implementations may return either the raw response object or a
            flattened list of approval steps, depending on the upstream shape.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_template_fields(self, template_id: str) -> list[TemplateFieldConfig]:
        """Fetch the field configuration for a governance template.

        Args:
            template_id: The governance template ID.

        Returns:
            The template's fields mapped onto :class:`TemplateFieldConfig`.
            Returns an empty list when the template has no fields.
        """
        raise NotImplementedError
