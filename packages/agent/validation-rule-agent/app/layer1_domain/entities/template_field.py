"""Domain entity: a governance template field.

Layer 1 — plain dataclass, no framework. Canonical home for TemplateField so
Layer-2 ports can reference it without depending on Layer 4.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TemplateField:
    table: str
    field: str
    alias: str
    section: str

    @staticmethod
    def build_header(fields: "list[TemplateField]") -> tuple[list[str], dict[str, object]]:
        """From governance template fields -> (aliases, empty header keyed by alias)."""
        aliases = [f.alias for f in fields]
        header: dict[str, object] = {alias: "" for alias in aliases}
        return aliases, header
