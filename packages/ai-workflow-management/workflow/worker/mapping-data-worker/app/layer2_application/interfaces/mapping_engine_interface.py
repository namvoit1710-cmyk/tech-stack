from typing import Any, Protocol

from app.layer1_domain.entities.mapping_rule import ArrayMappingConfig, MappingRule


class IMappingEngine(Protocol):
    """Interface for the mapping execution engine."""

    def map_data(
        self,
        source_data: dict[str, Any],
        mapping_rules: list[MappingRule],
        array_mappings: list[ArrayMappingConfig],
        strict_mode: bool = False,
        on_missing: str | None = None,
        on_type_error: str | None = None,
    ) -> dict[str, Any]:
        """Execute mapping rules against source data and return the result.

        SA-1813: ``on_missing`` (skip/null/error) and ``on_type_error``
        (keep/null/error) are optional; ``None`` derives the default from
        ``strict_mode`` (missing -> error/skip, type error -> error).
        """
        ...
