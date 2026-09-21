from __future__ import annotations

import json
from typing import Any


def parse_topic_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in stripped.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    coerced = str(value).strip()
    return [coerced] if coerced else []


def normalize_topics(
    topic: str | None = None,
    topics: list[str] | tuple[str, ...] | str | None = None,
) -> list[str]:
    resolved = parse_topic_list(topics)
    if topic and not resolved:
        resolved.extend(parse_topic_list(topic))
    return list(dict.fromkeys(resolved))


def has_static_callable_attr(target: Any, attribute_name: str) -> bool:
    instance_dict = getattr(target, "__dict__", None)
    if isinstance(instance_dict, dict) and attribute_name in instance_dict:
        return callable(instance_dict[attribute_name])

    for cls in type(target).__mro__:
        if attribute_name in cls.__dict__:
            return callable(getattr(target, attribute_name))

    return False
