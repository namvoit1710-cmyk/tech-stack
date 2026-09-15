import sys
import os
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto, BaseOutputDto


# --- Test DTOs ---

@dataclass
class SampleCommand:
    name: str = ""
    value: int = 0


@dataclass
class SampleResult:
    name: str = ""
    value: int = 0


class SampleInputDto(BaseInputDto):
    name: str = ""
    value: int = 0


class SampleOutputDto(BaseOutputDto):
    name: str = ""
    value: int = 0


def test_dataclass_to_pydantic():
    dto = SampleInputDto(name="test", value=42)
    command = dto.to_dataclass(SampleCommand)
    assert command.name == "test"
    assert command.value == 42


def test_pydantic_from_dataclass():
    result = SampleResult(name="hello", value=99)
    dto = SampleOutputDto.from_dataclass(result)
    assert dto.name == "hello"
    assert dto.value == 99
