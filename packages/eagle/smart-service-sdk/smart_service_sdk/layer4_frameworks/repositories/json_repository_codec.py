from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class JsonRepositoryCodec:
    def dump_json(self, value: object, *, sort_keys: bool = False) -> str:
        return json.dumps(value, sort_keys=sort_keys)

    def dump_vector(self, value: Iterable[object]) -> str:
        return json.dumps(
            [float(cast(Any, item)) for item in value], separators=(",", ":")
        )

    def load_json(self, value: str | None) -> object:
        if not value:
            return {}
        return json.loads(value)

    def load_json_object(self, value: str | None) -> dict[str, object]:
        loaded = self.load_json(value)
        if isinstance(loaded, dict):
            return {str(key): item for key, item in loaded.items()}
        return {}

    def load_json_array(self, value: str | None) -> list[object]:
        loaded = self.load_json(value)
        if isinstance(loaded, list):
            return list(loaded)
        return []
