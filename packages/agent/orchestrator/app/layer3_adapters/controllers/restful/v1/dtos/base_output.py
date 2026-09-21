"""Base output DTO following blueprint convention."""

class BaseOutput:
    """Base class for all output DTOs.

    Subclasses should implement from_dataclass() to bridge
    domain dataclass -> HTTP response.
    """

    @classmethod
    def from_dataclass(cls, obj):
        raise NotImplementedError
