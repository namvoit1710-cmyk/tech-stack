from pydantic import BaseModel
from typing import TypeVar, Any

T = TypeVar("T")


class BaseInputDto(BaseModel):
    def to_dataclass(self, dataclass_cls: type[T]) -> T:
        return dataclass_cls(**self.model_dump())


class BaseOutputDto(BaseModel):
    @classmethod
    def from_dataclass(cls, dataclass_obj: Any) -> "BaseOutputDto":
        return cls(**dataclass_obj.__dict__)
