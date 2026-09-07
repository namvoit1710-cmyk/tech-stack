from pydantic import BaseModel


class BaseInputDto(BaseModel):
    pass


class BaseOutputDto(BaseModel):
    success: bool
    message: str = ""
