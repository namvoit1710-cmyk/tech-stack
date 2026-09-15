from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["MappingRule", "ArrayMappingConfig"]


@dataclass
class MappingRule:
    """A single field-to-field mapping rule."""
    source_path: str
    target_field: str
    field_type: str = "string"
    default_value: Optional[Any] = None
    mapping_mode: str = "direct"


@dataclass
class ArrayMappingConfig:
    """Configuration for mapping items within a source array.

    ``output_mode`` controls the shape of the mapped result:

    * ``"objects"`` (default) each source item is mapped to a dict via
      ``item_mappings``, producing ``List[Dict]``.
    * ``"values"`` a single field is extracted from every source item
      (using ``pluck_field``) to produce a flat ``List[Any]``.
    """
    source_array_path: str
    target_field: str
    item_mappings: list[MappingRule] = field(default_factory=list)
    output_mode: str = "objects"   # "objects" | "values"
    pluck_field: Optional[str] = None
