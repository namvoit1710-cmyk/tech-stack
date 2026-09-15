"""Control-specific props for each field control type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class InputControlProps:
    """Props for InputControl."""
    placeholder: Optional[str] = None
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    class_name: Optional[str] = None
    input_type: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.placeholder is not None:
            result["placeholder"] = self.placeholder
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.class_name is not None:
            result["className"] = self.class_name
        if self.input_type is not None:
            result["type"] = self.input_type
        return result


@dataclass
class TextareaControlProps:
    """Props for TextareaControl."""
    placeholder: Optional[str] = None
    rows_length: Optional[int] = None
    growing: Optional[bool] = None
    growing_max_rows: Optional[int] = None
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.placeholder is not None:
            result["placeholder"] = self.placeholder
        if self.rows_length is not None:
            result["rowsLength"] = self.rows_length
        if self.growing is not None:
            result["growing"] = self.growing
        if self.growing_max_rows is not None:
            result["growingMaxRows"] = self.growing_max_rows
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class SelectOption:
    """A single option in a Select dropdown."""
    id: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "value": self.value}


@dataclass
class SelectControlProps:
    """Props for SelectControl."""
    placeholder: Optional[str] = None
    options: Optional[list[SelectOption]] = None
    value_key: Optional[str] = None
    label_key: Optional[str] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.placeholder is not None:
            result["placeholder"] = self.placeholder
        if self.options is not None:
            result["options"] = [o.to_dict() for o in self.options]
        if self.value_key is not None:
            result["valueKey"] = self.value_key
        if self.label_key is not None:
            result["labelKey"] = self.label_key
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class SwitchControlProps:
    """Props for SwitchControl."""
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class NodePopupControlProps:
    """Props for NodePopupControl."""
    label: str
    node_popup: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"label": self.label}
        if self.node_popup is not None:
            result["nodePopup"] = self.node_popup
        return result


@dataclass
class CheckBoxControlProps:
    """Props for CheckBoxControl."""
    label: Optional[str] = None
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.label is not None:
            result["label"] = self.label
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class ComboBoxControlProps:
    """Props for ComboBoxControl."""
    placeholder: Optional[str] = None
    options: Optional[list[SelectOption]] = None
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.placeholder is not None:
            result["placeholder"] = self.placeholder
        if self.options is not None:
            result["options"] = [o.to_dict() for o in self.options]
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class MultipleComboboxControlProps:
    """Props for MultipleComboboxControl."""
    placeholder: Optional[str] = None
    options: Optional[list[SelectOption]] = None
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.placeholder is not None:
            result["placeholder"] = self.placeholder
        if self.options is not None:
            result["options"] = [o.to_dict() for o in self.options]
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class RadioPopupControlProps:
    """Props for RadioPopupControl."""
    radio: Optional[dict[str, Any]] = None
    node_popup: Optional[dict[str, Any]] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.radio is not None:
            result["radio"] = self.radio
        if self.node_popup is not None:
            result["nodePopup"] = self.node_popup
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class SingleUploadControlProps:
    """Props for UploadControl."""
    hint: Optional[str] = None
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    upload_api_config: Optional[dict[str, Any]] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.hint is not None:
            result["hint"] = self.hint
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.upload_api_config is not None:
            result["uploadApiConfig"] = self.upload_api_config
        if self.class_name is not None:
            result["className"] = self.class_name
        return result


@dataclass
class SingleUploadFieldMappingControlProps:
    """Props for SingleUploadFieldMappingControl."""
    hint: Optional[str] = None
    readonly: Optional[bool] = None
    disabled: Optional[bool] = None
    upload_api_config: Optional[dict[str, Any]] = None
    class_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.hint is not None:
            result["hint"] = self.hint
        if self.readonly is not None:
            result["readonly"] = self.readonly
        if self.disabled is not None:
            result["disabled"] = self.disabled
        if self.upload_api_config is not None:
            result["uploadApiConfig"] = self.upload_api_config
        if self.class_name is not None:
            result["className"] = self.class_name
        return result
