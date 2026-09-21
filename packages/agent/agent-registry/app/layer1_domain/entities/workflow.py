"""Workflow entity - Represents a workflow that agents can execute.

This is a pure domain entity with NO external dependencies.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.layer1_domain.exceptions import InvalidDataException


@dataclass
class Workflow:
    """Workflow entity representing a workflow in the system.

    Domain invariants:
    - name must be 1-255 characters
    - version must not be empty
    """

    id: str
    name: str
    description: str
    version: str
    status: str
    main_flow: bool
    input_schema: list | None = None
    output_schema: list | None = None
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def equals_content(self, other: "Workflow") -> bool:
        """Check if the content of two workflows is equal, ignoring certain fields.

        Args:
            other (Workflow): The other workflow to compare with.

        Returns:
            bool: True if the content is equal (ignoring created_at and updated_at), False otherwise.
        """
        if not isinstance(other, Workflow):
            return False

        self_dict = asdict(self)
        other_dict = asdict(other)

        ignored_fields = {"created_at", "updated_at"}

        for ignored in ignored_fields:
            self_dict.pop(ignored, None)
            other_dict.pop(ignored, None)

        return self_dict == other_dict

    def update_attribute(self, field_name: str, value) -> bool:
        """Generic method to update an attribute.

        Args:
            field_name: Name of the attribute to update
            value: New value for the attribute

        Raises:
            InvalidDataException: If field_name is invalid
        """

        if not hasattr(self, field_name):
            raise InvalidDataException(f"Invalid field name: {field_name}", field=field_name)
        
        # Skip update if value is None or same as current (no-op)
        if value is None or getattr(self, field_name) == value:
            return False
        
        # Pre-process value
        if isinstance(value, str):
            value = value.strip()
        
        # Set the new value
        setattr(self, field_name, value)
        
        return True

    @classmethod
    def create(
        cls,
        id: str,
        name: str,
        description: str,
        version: str | None,
        status: str | None,
        main_flow: bool | None = None,
        input_schema: list | None = None,
        output_schema: list | None = None,
        metadata: dict | None = None
    ) -> "Workflow":
        """Factory method to create a new Workflow with business rule validation.

        Args:
            name: Workflow name (1-255 chars)
            description: Workflow description
            version: Workflow version
            status: Workflow status string

        Returns:
            New Workflow instance

        Raises:
            InvalidDataException: If name or version is invalid
        """
        # Create workflow instance
        workflow = cls(
            id=id,
            name=(name or "").strip(),
            description=description,
            version=(version or "").strip(),
            status=(status or "").strip(),
            main_flow=False if main_flow is None else main_flow,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            input_schema=input_schema,
            output_schema=output_schema,
            metadata=metadata or {},
        )

        return workflow

    def update(self, **kwargs) -> bool:
        """Update workflow fields with re-validation.

        Args:
            name: New workflow name (optional)
            description: New workflow description (optional)
            main_flow: New main flow flag (optional)
            version: New workflow version (optional)
            status: New workflow status (optional)
            output_schema: New output schema (optional)
            input_schema: New input schema (optional)
            metadata: New metadata (optional)

        Raises:
            InvalidDataException: If validation fails
        """
        changed = False

        for field_name, value in kwargs.items():
            changed = self.update_attribute(field_name, value) or changed

        if changed:
            self.updated_at = datetime.now(timezone.utc)

        return changed
