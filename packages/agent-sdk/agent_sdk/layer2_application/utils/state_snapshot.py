from copy import deepcopy
from typing import Any, Iterable

_SHARED_STATE_KEYS = (
    "shared_state",
    "shared_state_key",
    "shared_state_version",
)


def extract_state_snapshot(
    state: dict[str, Any],
    keys: Iterable[str] = (),
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in (*_SHARED_STATE_KEYS, *tuple(keys)):
        if key in state:
            snapshot[key] = deepcopy(state[key])
    return snapshot


def merge_state_snapshot(
    parent_state: dict[str, Any],
    child_state: dict[str, Any],
    keys: Iterable[str] = (),
) -> dict[str, Any]:
    merged_state = deepcopy(parent_state)
    for key in (*_SHARED_STATE_KEYS, *tuple(keys)):
        if key in child_state:
            merged_state[key] = deepcopy(child_state[key])
    return merged_state
