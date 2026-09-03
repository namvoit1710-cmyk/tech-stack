from dataclasses import asdict, dataclass
from pydantic import BaseModel
from typing import Any, TypeVar

T = TypeVar("T")


class BaseInputDto(BaseModel):
    def to_dataclass(self, dataclass_cls: type[T]) -> T:
        """Convert pydantic model to dataclass."""
        return dataclass_cls(**self.model_dump())


@dataclass
class BaseOutputDataclass:
    def to_pydantic(self, pydantic_cls: type[T]) -> T:
        """Convert dataclass to pydantic model."""
        return pydantic_cls(**asdict(self))


class BaseOutputDto(BaseModel):
    @classmethod
    def from_dataclass(cls, dataclass_obj: Any) -> "BaseOutputDto":
        """Convert dataclass to pydantic model."""
        return cls(**dataclass_obj.__dict__)
