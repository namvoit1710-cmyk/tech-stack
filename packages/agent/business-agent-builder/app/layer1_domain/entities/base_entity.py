import inspect
from typing import Mapping, Any, Type, TypeVar, cast

T = TypeVar("T", bound="BaseEntity")


class BaseEntity:
    @classmethod
    def from_mapping(cls: Type[T], value: Mapping[str, Any] | T | None = None) -> T:
        if isinstance(value, cls):
            return value

        mapping_value = cast(Mapping[str, Any] | None, value)
        data: dict[str, Any] = dict(mapping_value) if mapping_value is not None else {}
        init_signature = inspect.signature(cls.__init__)
        valid_keys = init_signature.parameters.keys()

        filtered_data: dict[str, Any] = {
            k: v for k, v in data.items()
            if k in valid_keys and k != "self"
        }
        return cls(**filtered_data)