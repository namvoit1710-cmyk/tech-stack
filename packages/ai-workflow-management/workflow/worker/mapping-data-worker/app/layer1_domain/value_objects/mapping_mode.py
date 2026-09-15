from enum import Enum


class MappingMode(str, Enum):
    """How a mapping rule resolves its value.

    SA-1812: the dead ``ARRAY_MAP`` member was removed. It was never handled by
    the engine (``_resolve_rule_value`` only special-cases ``CONSTANT``, so
    ``array_map`` fell through to the DIRECT path) and was never offered as a
    form option. Array reshaping is configured via ``array_mappings`` / the array
    node, not a per-rule mode.
    """
    DIRECT = "direct"
    CONSTANT = "constant"
