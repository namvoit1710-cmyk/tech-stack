from worker_sdk.layer1_domain.auto_form_mapper.enums import (
    FieldControl,
    FieldWrapper,
    OutputType,
)
from worker_sdk.layer1_domain.auto_form_mapper.helpers import (
    array_field,
    checkbox_field,
    combobox_field,
    hidden_field,
    markdown_field,
    multiple_combobox_field,
    node_popup_field,
    number_field,
    object_field,
    radio_popup_field,
    select_field,
    single_upload_field,
    single_upload_field_mapping_field,
    switch_field,
    table_array_field,
    text_field,
    textarea_field,
)
from worker_sdk.layer1_domain.auto_form_mapper.validation import required, required_string


# --- text_field ---

def test_text_field_basic():
    f = text_field("name", "Full Name")
    assert f.key == "name"
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.INPUT
    assert f.field_config.field_wrapper == FieldWrapper.FORM_ITEM
    assert f.field_config.wrapper_props == {"label": "Full Name"}
    assert f.field_config.control_props is None
    assert f.field_config.rules is None


def test_text_field_with_placeholder():
    f = text_field("name", "Name", placeholder="Enter name")
    assert f.field_config.control_props == {"placeholder": "Enter name"}


def test_text_field_with_rules_and_default():
    rules = required_string(2, 100)
    f = text_field("name", "Name", rules=rules, default="John")
    assert f.field_config.rules is rules
    assert f.field_config.control_props["value"] == "John"
    assert f.field_config.wrapper_props == {"label": "Name", "required": True}


def test_text_field_readonly_disabled():
    f = text_field("name", "Name", readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


# --- number_field ---

def test_number_field_basic():
    f = number_field("age", "Age")
    assert f.key == "age"
    assert f.output_type == OutputType.NUMBER
    assert f.field_config.control_props == {"type": "number"}
    assert f.field_config.wrapper_props == {"label": "Age"}


def test_number_field_with_placeholder():
    f = number_field("qty", "Quantity", placeholder="Enter qty")
    assert f.field_config.control_props == {"type": "number", "placeholder": "Enter qty"}


def test_number_field_with_default():
    f = number_field("price", "Price", default=9.99)
    assert f.field_config.control_props["value"] == 9.99


def test_number_field_readonly_disabled():
    f = number_field("qty", "Qty", readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


# --- textarea_field ---

def test_textarea_field_basic():
    f = textarea_field("desc", "Description")
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.TEXTAREA
    assert f.field_config.control_props == {"rowsLength": 3}
    assert f.field_config.wrapper_props == {"label": "Description"}


def test_textarea_field_custom():
    f = textarea_field("notes", "Notes", placeholder="Write...", rows=5, growing=True)
    assert f.field_config.control_props == {
        "rowsLength": 5,
        "placeholder": "Write...",
        "growing": True,
    }


def test_textarea_field_uses_rows_length_key():
    """Verify output uses 'rowsLength' (matching frontend), not 'rows'."""
    f = textarea_field("desc", "Desc", rows=7)
    cp = f.field_config.control_props
    assert "rowsLength" in cp
    assert "rows" not in cp
    assert cp["rowsLength"] == 7


def test_textarea_field_readonly_disabled():
    f = textarea_field("desc", "Desc", readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


# --- select_field ---

def test_select_field_basic():
    f = select_field("role", "Role", [("admin", "Admin"), ("user", "User")])
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.SELECT
    cp = f.field_config.control_props
    assert cp["placeholder"] == "Select role"
    assert len(cp["options"]) == 2
    assert cp["options"][0] == {"id": "admin", "value": "Admin"}
    assert f.field_config.wrapper_props == {"label": "Role"}


def test_select_field_custom_placeholder():
    f = select_field("x", "X", [("a", "A")], placeholder="Pick one")
    assert f.field_config.control_props["placeholder"] == "Pick one"


def test_select_field_with_rules():
    f = select_field("x", "X", [("a", "A")], rules=required())
    assert f.field_config.wrapper_props == {"label": "X", "required": True}
    assert len(f.field_config.rules) == 1


# --- switch_field ---

def test_switch_field_basic():
    f = switch_field("active", "Active")
    assert f.output_type == OutputType.BOOLEAN
    assert f.field_config.field_control == FieldControl.SWITCH
    assert f.field_config.control_props is None
    assert f.field_config.wrapper_props == {"label": "Active"}


def test_switch_field_default_true():
    f = switch_field("enabled", "Enabled", default=True)
    assert f.field_config.control_props == {"value": True}


def test_switch_field_readonly_disabled():
    f = switch_field("active", "Active", readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


# --- hidden_field ---

def test_hidden_field():
    f = hidden_field("secret", default="abc")
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control is None
    assert f.field_config.control_props == {"value": "abc"}


def test_hidden_field_no_default():
    f = hidden_field("id")
    assert f.field_config.control_props is None


# --- markdown_field ---

def test_markdown_field():
    f = markdown_field("info", "# Hello", label="Info")
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.MARKDOWN
    assert f.field_config.control_props == {"value": "# Hello"}
    assert f.field_config.wrapper_props == {"label": "Info"}


def test_markdown_field_no_label():
    f = markdown_field("md", "content")
    assert f.field_config.wrapper_props is None
    assert f.field_config.control_props == {"value": "content"}


# --- array_field ---

def test_array_field_structure():
    items = [text_field("name", "Name")]
    f = array_field("people", "People", items)
    assert f.output_type == OutputType.ARRAY
    assert f.field_config.field_wrapper == FieldWrapper.SEQUENCE
    assert f.field_config.wrapper_props == {"label": "People"}
    assert len(f.fields) == 1
    template = f.fields[0]
    assert template.key == "0"
    assert template.output_type == OutputType.OBJECT
    assert template.field_config.field_wrapper == FieldWrapper.FRAGMENT
    assert len(template.fields) == 1
    assert template.fields[0].key == "name"


def test_array_field_custom_wrappers():
    f = array_field(
        "items", "Items",
        item_fields=[text_field("x", "X")],
        wrapper=FieldWrapper.TABLE,
        item_wrapper=FieldWrapper.DIV,
        item_wrapper_props={"className": "row"},
    )
    assert f.field_config.field_wrapper == FieldWrapper.TABLE
    assert f.fields[0].field_config.field_wrapper == FieldWrapper.DIV
    assert f.fields[0].field_config.wrapper_props == {"className": "row"}


# --- table_array_field ---

def test_table_array_field_structure():
    cols = [
        text_field("product", "Product"),
        number_field("qty", "Qty"),
    ]
    f = table_array_field("items", "Line Items", cols)
    assert f.output_type == OutputType.ARRAY
    assert f.field_config.field_wrapper == FieldWrapper.TABLE
    assert f.field_config.wrapper_props == {"label": "Line Items"}
    assert len(f.fields) == 1
    row = f.fields[0]
    assert row.key == "0"
    assert row.field_config.field_wrapper == FieldWrapper.TABLE_ROW
    assert len(row.fields) == 2
    assert row.fields[0].field_config.field_wrapper == FieldWrapper.TABLE_CELL
    assert row.fields[0].key == "product"
    assert row.fields[1].field_config.field_wrapper == FieldWrapper.TABLE_CELL
    assert row.fields[1].key == "qty"


def test_table_array_field_preserves_props():
    cols = [
        text_field("name", "Name", rules=required()),
    ]
    f = table_array_field("t", "T", cols)
    col = f.fields[0].fields[0]
    assert col.field_config.rules is not None
    assert col.field_config.wrapper_props == {"label": "Name", "required": True}
    assert col.field_config.field_control == FieldControl.INPUT


# --- object_field ---

def test_object_field_basic():
    children = [text_field("city", "City"), text_field("zip", "Zip")]
    f = object_field("address", "Address", children)
    assert f.output_type == OutputType.OBJECT
    assert f.field_config.wrapper_props == {"label": "Address"}
    assert f.field_config.field_wrapper == FieldWrapper.FRAGMENT
    assert len(f.fields) == 2


def test_object_field_custom_wrapper():
    f = object_field(
        "section", None,
        children=[text_field("x", "X")],
        wrapper=FieldWrapper.DIV,
        wrapper_props={"className": "section"},
    )
    assert f.field_config.field_wrapper == FieldWrapper.DIV
    assert f.field_config.wrapper_props == {"className": "section"}


def test_object_field_label_merged_with_wrapper_props():
    f = object_field(
        "section", "My Section",
        children=[text_field("x", "X")],
        wrapper_props={"className": "section"},
    )
    assert f.field_config.wrapper_props == {"className": "section", "label": "My Section"}


# --- checkbox_field ---

def test_checkbox_field_basic():
    f = checkbox_field("agree", "I Agree")
    assert f.output_type == OutputType.BOOLEAN
    assert f.field_config.field_control == FieldControl.CHECKBOX
    assert f.field_config.field_wrapper == FieldWrapper.FORM_ITEM
    assert f.field_config.wrapper_props == {"label": "I Agree"}
    assert f.field_config.control_props == {"label": "I Agree"}


def test_checkbox_field_with_rules():
    f = checkbox_field("agree", "I Agree", rules=required())
    assert f.field_config.wrapper_props == {"label": "I Agree", "required": True}
    assert len(f.field_config.rules) == 1


def test_checkbox_field_readonly_disabled():
    f = checkbox_field("agree", "I Agree", readonly=True, disabled=True)
    cp = f.field_config.control_props
    assert cp["label"] == "I Agree"
    assert cp["readonly"] is True
    assert cp["disabled"] is True


# --- combobox_field ---

def test_combobox_field_basic():
    f = combobox_field("country", "Country", [("us", "USA"), ("uk", "UK")])
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.COMBOBOX
    assert f.field_config.control_props["placeholder"] == "Select country"
    assert len(f.field_config.control_props["options"]) == 2


def test_combobox_field_custom_placeholder():
    f = combobox_field("x", "X", [("a", "A")], placeholder="Pick")
    assert f.field_config.control_props["placeholder"] == "Pick"


def test_combobox_field_readonly_disabled():
    f = combobox_field("x", "X", [("a", "A")], readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


# --- multiple_combobox_field ---

def test_multiple_combobox_field_basic():
    f = multiple_combobox_field("tags", "Tags", [("a", "A"), ("b", "B")])
    assert f.output_type == OutputType.ARRAY
    assert f.field_config.field_control == FieldControl.MULTIPLE_COMBOBOX
    assert len(f.field_config.control_props["options"]) == 2


def test_multiple_combobox_field_readonly_disabled():
    f = multiple_combobox_field("tags", "Tags", [("a", "A")], readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


# --- radio_popup_field ---

def test_radio_popup_field_basic():
    f = radio_popup_field("choice", "Choice")
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.RADIO_POPUP
    assert f.field_config.wrapper_props == {"label": "Choice"}
    assert f.field_config.initial_api_config is None


def test_radio_popup_field_with_api():
    f = radio_popup_field("choice", "Choice", initial_api_config={"endpoint": "/api"})
    assert f.field_config.initial_api_config == {"endpoint": "/api"}


def test_radio_popup_field_with_radio_and_popup():
    radio = {"name": "opt", "label": "Option A", "value": "a"}
    popup = {"initialApiConfig": {"endpoint": "/schema"}}
    f = radio_popup_field("choice", "Choice", radio=radio, node_popup=popup)
    assert f.field_config.control_props["radio"] == radio
    assert f.field_config.control_props["nodePopup"] == popup


# --- single_upload_field ---

def test_single_upload_field_basic():
    f = single_upload_field("file", "Upload File")
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.SINGLE_UPLOAD
    assert f.field_config.wrapper_props == {"label": "Upload File"}


def test_single_upload_field_with_api():
    f = single_upload_field("file", "File", upload_api_config={"endpoint": "/upload"})
    assert f.field_config.control_props["uploadApiConfig"] == {"endpoint": "/upload"}


def test_single_upload_field_with_hint():
    f = single_upload_field("file", "File", hint="Drag and drop here")
    assert f.field_config.control_props["hint"] == "Drag and drop here"


def test_single_upload_field_readonly_disabled():
    f = single_upload_field("file", "File", readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


# --- node_popup_field ---

def test_node_popup_field_basic():
    f = node_popup_field("config", "Configure")
    assert f.output_type == OutputType.STRING
    assert f.field_config.field_control == FieldControl.NODE_POPUP
    assert f.field_config.field_wrapper == FieldWrapper.FORM_ITEM
    assert f.field_config.wrapper_props == {"label": "Configure"}
    assert f.field_config.control_props == {"label": "Configure"}


def test_node_popup_field_with_popup():
    popup = {"initialApiConfig": {"endpoint": "/api/schema"}}
    f = node_popup_field("config", "Configure", node_popup=popup)
    assert f.field_config.control_props["label"] == "Configure"
    assert f.field_config.control_props["nodePopup"] == popup


def test_node_popup_field_with_rules():
    f = node_popup_field("config", "Configure", rules=required())
    assert f.field_config.wrapper_props == {"label": "Configure", "required": True}
    assert len(f.field_config.rules) == 1


# --- single_upload_field_mapping_field ---

def test_single_upload_field_mapping_field_basic():
    f = single_upload_field_mapping_field("mapping", "Field Mapping")
    assert f.output_type == OutputType.OBJECT
    assert f.field_config.field_control == FieldControl.SINGLE_UPLOAD_FIELD_MAPPING
    assert f.field_config.field_wrapper == FieldWrapper.FORM_ITEM
    assert f.field_config.wrapper_props == {"label": "Field Mapping"}
    assert f.field_config.control_props is None


def test_single_upload_field_mapping_field_with_api_and_hint():
    api = {"endpoint": "/upload"}
    f = single_upload_field_mapping_field("mapping", "Mapping", upload_api_config=api, hint="Upload Excel")
    assert f.field_config.control_props["hint"] == "Upload Excel"
    assert f.field_config.control_props["uploadApiConfig"] == api


def test_single_upload_field_mapping_field_readonly_disabled():
    f = single_upload_field_mapping_field("mapping", "Mapping", readonly=True, disabled=True)
    assert f.field_config.control_props["readonly"] is True
    assert f.field_config.control_props["disabled"] is True


def test_single_upload_field_mapping_field_with_rules():
    f = single_upload_field_mapping_field("mapping", "Mapping", rules=required())
    assert f.field_config.wrapper_props == {"label": "Mapping", "required": True}
    assert len(f.field_config.rules) == 1
