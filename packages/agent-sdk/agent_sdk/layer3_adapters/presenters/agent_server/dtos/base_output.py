from pydantic import BaseModel


class BaseOutputPydantic(BaseModel):
    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "BaseOutputPydantic":
        return cls(**dataclass_obj.__dict__)
