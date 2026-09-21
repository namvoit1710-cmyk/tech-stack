import json
import logging
from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from typing import Any, Callable, Optional, Type, cast, get_args, get_origin, get_type_hints

from openai import OpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _is_pydantic_model(model_type: type[Any]) -> bool:
    return isinstance(model_type, type) and issubclass(model_type, BaseModel)


def _is_dataclass_type(model_type: type[Any]) -> bool:
    return isinstance(model_type, type) and is_dataclass(model_type)


def _materialize_value(value: Any, target_type: Any) -> Any:
    origin = get_origin(target_type)

    if target_type is Any or target_type is object:
        return value

    if origin is None:
        if isinstance(target_type, type) and issubclass(target_type, Enum):
            if isinstance(value, target_type):
                return value
            try:
                return target_type(value)
            except ValueError:
                if isinstance(value, str):
                    try:
                        return target_type[value]
                    except KeyError as exc:
                        raise ValueError(
                            f"{value!r} is not a valid {target_type.__name__}"
                        ) from exc
                raise

        if _is_dataclass_type(target_type):
            return _materialize_dataclass(cast(type[Any], target_type), value)

        if target_type is type(None):
            if value is not None:
                raise ValueError("Expected null value")
            return None

        return value

    if origin in (list, tuple):
        item_type = get_args(target_type)[0] if get_args(target_type) else Any
        if isinstance(value, list):
            items = [_materialize_value(item, item_type) for item in value]
            return items if origin is list else tuple(items)
        if isinstance(value, dict):
            single_item = _materialize_value(value, item_type)
            return [single_item] if origin is list else (single_item,)
        raise ValueError(f"Expected list payload, received {type(value).__name__}")

    if origin is dict:
        _, value_type = get_args(target_type) if get_args(target_type) else (Any, Any)
        if not isinstance(value, dict):
            raise ValueError(f"Expected object payload, received {type(value).__name__}")
        return {key: _materialize_value(item, value_type) for key, item in value.items()}

    if origin is cast(Any, __import__("typing")).Union:
        union_args = [arg for arg in get_args(target_type) if arg is not type(None)]
        if value is None and len(union_args) != len(get_args(target_type)):
            return None
        last_error: Exception | None = None
        for union_arg in union_args:
            try:
                return _materialize_value(value, union_arg)
            except Exception as exc:  # pragma: no cover - best effort branch selection
                last_error = exc
        raise ValueError(f"Value did not match any supported union member: {last_error}")

    return value


def _materialize_dataclass(model_type: type[Any], payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object payload for {model_type.__name__}, received {type(payload).__name__}")

    type_hints = get_type_hints(model_type)
    model_kwargs: dict[str, Any] = {}

    for field in fields(model_type):
        field_type = type_hints.get(field.name, field.type)
        if field.name not in payload:
            if field.default is not MISSING or field.default_factory is not MISSING:
                continue
            raise ValueError(f"Missing field '{field.name}' for {model_type.__name__}")
        model_kwargs[field.name] = _materialize_value(payload[field.name], field_type)

    return model_type(**model_kwargs)

class OpenAILLMProvider:
    """Classifies user intent via OpenAI Chat Completions (gpt-4o-mini)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self._client = OpenAI(api_key=api_key)
        self._model = model
        # SA-2079: optional hook invoked with the raw `completion.usage` after every call, so a
        # wrapper (InstrumentedLLMProvider) can surface token usage without changing this
        # method's return type / existing call sites.
        self.usage_observer: Optional[Callable[[Any], None]] = None

    def _notify_usage(self, completion: Any) -> None:
        if self.usage_observer is not None:
            self.usage_observer(getattr(completion, "usage", None))

    def complete_with_format(
        self,
        *,
        messages: list[dict[str, str]],
        response_model: Type[Any],
    ) -> Any:
        """Parse a structured response into a Pydantic model or dataclass instance."""
        if _is_pydantic_model(response_model):
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=cast(Any, messages),
                response_format=response_model,
            )
            self._notify_usage(completion)
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise ValueError(f"OpenAI returned no parsed {response_model.__name__} payload")
            return parsed

        if _is_dataclass_type(response_model):
            dataclass_messages = [
                {
                    "role": "system",
                    "content": (
                        "Return only valid JSON matching the requested response structure. "
                        "Do not include markdown fences or explanatory text."
                    ),
                },
                *messages,
            ]
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=cast(Any, dataclass_messages),
                response_format={"type": "json_object"},
            )
            self._notify_usage(completion)
            content = completion.choices[0].message.content
            if not content:
                raise ValueError(f"OpenAI returned no JSON content for {response_model.__name__}")
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"OpenAI returned invalid JSON for {response_model.__name__}: {exc}"
                ) from exc
            return _materialize_dataclass(response_model, payload)

        raise TypeError(
            "response_model must be a Pydantic BaseModel subtype or a dataclass type"
        )
