"""Base DTO classes for REST API input and output."""

from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class BaseInputDto(BaseModel):
    """Base class for all request DTOs."""

    def to_dataclass(self, dataclass_cls: type[T]) -> T:
        """Convert Pydantic model to a use-case Command dataclass."""
        return dataclass_cls(**self.model_dump())


class BaseOutputDto(BaseModel):
    """Base class for all response DTOs."""

    @classmethod
    def from_dataclass(cls, dataclass_obj: Any) -> "BaseOutputDto":
        """Create a DTO from a use-case Result dataclass."""
        return cls(**dataclass_obj.__dict__)
