"""Base input DTO following blueprint convention."""

class BaseInput:
    """Base class for all input DTOs.

    Subclasses should implement to_dataclass() to bridge
    HTTP request -> domain dataclass.
    """

    def to_dataclass(self):
        raise NotImplementedError
