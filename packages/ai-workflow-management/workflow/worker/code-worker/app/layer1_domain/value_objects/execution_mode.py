from enum import Enum


class ExecutionMode(str, Enum):
    """How the user script is invoked relative to the incoming items.

    ALL_ITEMS:  the script runs once and receives the full ``items`` list.
    PER_ITEM:   the script runs once per item; ``item`` is the current element.
    """
    ALL_ITEMS = "all_items"
    PER_ITEM = "per_item"
