from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class BaseInputPydantic(BaseModel):
    def to_dataclass(self, dataclass_cls: Type[T]) -> T:
        return dataclass_cls(**self.model_dump())
