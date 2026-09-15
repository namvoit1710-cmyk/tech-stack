"""Compare two objects recursively and yield changed paths."""

from typing import Any
from collections.abc import Iterable


def compare_changes(obj1: Any, obj2: Any, path: str = "") -> Iterable[tuple[str, Any, Any]]:
    """
    Generator đệ quy so sánh nested object.
    Yields (path, old_value, new_value) cho mỗi khác biệt.
    """
    if type(obj1) is not type(obj2):
        yield path, obj1, obj2
        return

    if isinstance(obj1, dict):
        #keys = set(obj1.keys()) | set(obj2.keys())
        keys = set(obj1.keys())
        for k in keys:
            new_path = f"{path}.{k}" if path else str(k)
            if k not in obj1:
                yield new_path, None, obj2[k]
            elif k not in obj2:
                yield new_path, obj1[k], None
            else:
                yield from compare_changes(obj1[k], obj2[k], new_path)

    elif isinstance(obj1, list):
        if len(obj1) != len(obj2):
            yield f"{path}[length]", len(obj1), len(obj2)

        for i, (v1, v2) in enumerate(zip(obj1, obj2)):
            yield from compare_changes(v1, v2, f"{path}[{i}]")

    else:
        if obj1 != obj2:
            yield path, obj1, obj2
