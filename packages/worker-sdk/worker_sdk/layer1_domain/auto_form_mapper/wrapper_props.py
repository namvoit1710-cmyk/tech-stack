"""Wrapper-specific props for each layout wrapper type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class FormItemWrapperProps:
    """Props for FormItemWrapper."""
    label: Optional[str] = None
    required: Optional[bool] = None
    label_span: Optional[str] = None
    field_span: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.label is not None:
            result["label"] = self.label
        if self.required is not None:
            result["required"] = self.required
        if self.label_span is not None:
            result["labelSpan"] = self.label_span
        if self.field_span is not None:
            result["fieldSpan"] = self.field_span
        return result


@dataclass
class DivWrapperProps:
    """Props for DivWrapper."""
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class SequenceWrapperProps:
    """Props for SequenceWrapper."""
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class TabItemWrapperProps:
    """Props for TabItemWrapper."""
    label: Optional[str] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.label is not None:
            result["label"] = self.label
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class TableWrapperProps:
    """Props for TableWrapper."""
    label: Optional[str] = None
    search: Optional[dict[str, Any]] = None
    header_column_props: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.label is not None:
            result["label"] = self.label
        if self.search is not None:
            result["search"] = self.search
        if self.header_column_props is not None:
            result["headerColumnProps"] = self.header_column_props
        return result
