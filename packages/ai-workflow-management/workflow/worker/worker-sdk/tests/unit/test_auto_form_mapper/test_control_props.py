from worker_sdk.layer1_domain.auto_form_mapper.control_props import (
    CheckBoxControlProps,
    ComboBoxControlProps,
    InputControlProps,
    MultipleComboboxControlProps,
    NodePopupControlProps,
    RadioPopupControlProps,
    SelectControlProps,
    SelectOption,
    SingleUploadControlProps,
    SingleUploadFieldMappingControlProps,
    SwitchControlProps,
    TextareaControlProps,
)


# --- InputControlProps ---

def test_input_control_props_all():
    props = InputControlProps(
        placeholder="Enter value",
        readonly=True,
        disabled=False,
        class_name="my-class",
        input_type="number",
    )
    d = props.to_dict()
    assert d == {
        "placeholder": "Enter value",
        "readonly": True,
        "disabled": False,
        "className": "my-class",
        "type": "number",
    }


def test_input_control_props_empty():
    props = InputControlProps()
    assert props.to_dict() == {}


def test_input_control_props_partial():
    props = InputControlProps(placeholder="Name")
    assert props.to_dict() == {"placeholder": "Name"}


def test_input_control_props_readonly_disabled():
    props = InputControlProps(readonly=True, disabled=True)
    assert props.to_dict() == {"readonly": True, "disabled": True}


# --- TextareaControlProps ---

def test_textarea_control_props_all():
    props = TextareaControlProps(
        placeholder="Write here",
        rows_length=5,
        growing=True,
        growing_max_rows=10,
        readonly=True,
        disabled=False,
        class_name="ta-class",
    )
    d = props.to_dict()
    assert d == {
        "placeholder": "Write here",
        "rowsLength": 5,
        "growing": True,
        "growingMaxRows": 10,
        "readonly": True,
        "disabled": False,
        "className": "ta-class",
    }


def test_textarea_control_props_empty():
    props = TextareaControlProps()
    assert props.to_dict() == {}


def test_textarea_control_props_rows_length_key():
    """Verify output uses 'rowsLength' (matching frontend), not 'rows'."""
    props = TextareaControlProps(rows_length=5)
    d = props.to_dict()
    assert "rowsLength" in d
    assert "rows" not in d
    assert d["rowsLength"] == 5


# --- SelectOption ---

def test_select_option_to_dict():
    opt = SelectOption(id="1", value="Option 1")
    assert opt.to_dict() == {"id": "1", "value": "Option 1"}


# --- SelectControlProps ---

def test_select_control_props_all():
    props = SelectControlProps(
        placeholder="Choose...",
        options=[SelectOption(id="a", value="A"), SelectOption(id="b", value="B")],
        value_key="id",
        label_key="value",
        class_name="sel-class",
    )
    d = props.to_dict()
    assert d["placeholder"] == "Choose..."
    assert len(d["options"]) == 2
    assert d["options"][0] == {"id": "a", "value": "A"}
    assert d["valueKey"] == "id"
    assert d["labelKey"] == "value"
    assert d["className"] == "sel-class"


def test_select_control_props_empty():
    props = SelectControlProps()
    assert props.to_dict() == {}


# --- SwitchControlProps ---

def test_switch_control_props_all():
    props = SwitchControlProps(readonly=True, disabled=False, class_name="sw-class")
    assert props.to_dict() == {"readonly": True, "disabled": False, "className": "sw-class"}


def test_switch_control_props_with_class():
    props = SwitchControlProps(class_name="sw-class")
    assert props.to_dict() == {"className": "sw-class"}


def test_switch_control_props_empty():
    props = SwitchControlProps()
    assert props.to_dict() == {}


def test_switch_control_props_readonly_disabled():
    props = SwitchControlProps(readonly=True, disabled=True)
    assert props.to_dict() == {"readonly": True, "disabled": True}


# --- NodePopupControlProps ---

def test_node_popup_control_props_label_only():
    props = NodePopupControlProps(label="Click to open")
    assert props.to_dict() == {"label": "Click to open"}


def test_node_popup_control_props_with_node_popup():
    popup = {"initialApiConfig": {"endpoint": "/api/schema"}}
    props = NodePopupControlProps(label="Open Form", node_popup=popup)
    d = props.to_dict()
    assert d["label"] == "Open Form"
    assert d["nodePopup"] == popup


# --- CheckBoxControlProps ---

def test_checkbox_control_props_all():
    props = CheckBoxControlProps(label="I Agree", readonly=True, disabled=False, class_name="cb-class")
    assert props.to_dict() == {
        "label": "I Agree",
        "readonly": True,
        "disabled": False,
        "className": "cb-class",
    }


def test_checkbox_control_props_with_class():
    props = CheckBoxControlProps(class_name="cb-class")
    assert props.to_dict() == {"className": "cb-class"}


def test_checkbox_control_props_empty():
    props = CheckBoxControlProps()
    assert props.to_dict() == {}


def test_checkbox_control_props_label():
    props = CheckBoxControlProps(label="Accept terms")
    assert props.to_dict() == {"label": "Accept terms"}


# --- ComboBoxControlProps ---

def test_combobox_control_props_all():
    props = ComboBoxControlProps(
        placeholder="Search...",
        options=[SelectOption(id="a", value="A")],
        readonly=True,
        disabled=False,
        class_name="combo-cls",
    )
    d = props.to_dict()
    assert d["placeholder"] == "Search..."
    assert len(d["options"]) == 1
    assert d["readonly"] is True
    assert d["disabled"] is False
    assert d["className"] == "combo-cls"


def test_combobox_control_props_empty():
    props = ComboBoxControlProps()
    assert props.to_dict() == {}


def test_combobox_control_props_readonly_disabled():
    props = ComboBoxControlProps(readonly=True, disabled=True)
    assert props.to_dict() == {"readonly": True, "disabled": True}


# --- MultipleComboboxControlProps ---

def test_multiple_combobox_control_props_all():
    props = MultipleComboboxControlProps(
        placeholder="Search...",
        options=[SelectOption(id="x", value="X")],
        readonly=True,
        disabled=False,
        class_name="multi-cls",
    )
    d = props.to_dict()
    assert d["placeholder"] == "Search..."
    assert len(d["options"]) == 1
    assert d["readonly"] is True
    assert d["disabled"] is False
    assert d["className"] == "multi-cls"


def test_multiple_combobox_control_props_empty():
    props = MultipleComboboxControlProps()
    assert props.to_dict() == {}


# --- RadioPopupControlProps ---

def test_radio_popup_control_props_all():
    radio = {"name": "choice", "label": "Option A", "value": "a"}
    popup = {"initialApiConfig": {"endpoint": "/api"}}
    props = RadioPopupControlProps(radio=radio, node_popup=popup, class_name="rp-class")
    d = props.to_dict()
    assert d["radio"] == radio
    assert d["nodePopup"] == popup
    assert d["className"] == "rp-class"


def test_radio_popup_control_props_with_class():
    props = RadioPopupControlProps(class_name="rp-class")
    assert props.to_dict() == {"className": "rp-class"}


def test_radio_popup_control_props_empty():
    props = RadioPopupControlProps()
    assert props.to_dict() == {}


# --- SingleUploadControlProps ---

def test_single_upload_control_props_all():
    api_config = {"endpoint": "/upload", "method": "post"}
    props = SingleUploadControlProps(
        hint="Drop file here",
        readonly=True,
        disabled=False,
        upload_api_config=api_config,
        class_name="su-class",
    )
    d = props.to_dict()
    assert d == {
        "hint": "Drop file here",
        "readonly": True,
        "disabled": False,
        "uploadApiConfig": api_config,
        "className": "su-class",
    }


def test_single_upload_control_props_with_class():
    props = SingleUploadControlProps(class_name="su-class")
    assert props.to_dict() == {"className": "su-class"}


def test_single_upload_control_props_empty():
    props = SingleUploadControlProps()
    assert props.to_dict() == {}


# --- SingleUploadFieldMappingControlProps ---

def test_single_upload_field_mapping_control_props_all():
    api_config = {"endpoint": "/upload", "method": "post"}
    props = SingleUploadFieldMappingControlProps(
        hint="Upload mapping file",
        readonly=True,
        disabled=False,
        upload_api_config=api_config,
        class_name="sufm-class",
    )
    d = props.to_dict()
    assert d == {
        "hint": "Upload mapping file",
        "readonly": True,
        "disabled": False,
        "uploadApiConfig": api_config,
        "className": "sufm-class",
    }


def test_single_upload_field_mapping_control_props_with_class():
    props = SingleUploadFieldMappingControlProps(class_name="sufm-class")
    assert props.to_dict() == {"className": "sufm-class"}


def test_single_upload_field_mapping_control_props_empty():
    props = SingleUploadFieldMappingControlProps()
    assert props.to_dict() == {}
