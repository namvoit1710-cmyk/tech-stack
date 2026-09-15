from enum import Enum


class MappingFieldType(str, Enum):
    """Target field types for data mapping."""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
