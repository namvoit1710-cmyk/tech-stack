from worker_sdk.layer1_domain.auto_form_mapper.wrapper_props import (
    DivWrapperProps,
    FormItemWrapperProps,
    SequenceWrapperProps,
    TabItemWrapperProps,
    TableWrapperProps,
)


# --- FormItemWrapperProps ---

def test_form_item_wrapper_props_all():
    props = FormItemWrapperProps(label="Name", required=True, label_span="S4", field_span="S8")
    d = props.to_dict()
    assert d == {"label": "Name", "required": True, "labelSpan": "S4", "fieldSpan": "S8"}


def test_form_item_wrapper_props_empty():
    props = FormItemWrapperProps()
    assert props.to_dict() == {}


def test_form_item_wrapper_props_label_only():
    props = FormItemWrapperProps(label="Email")
    assert props.to_dict() == {"label": "Email"}


def test_form_item_wrapper_props_partial():
    props = FormItemWrapperProps(label_span="S6")
    assert props.to_dict() == {"labelSpan": "S6"}


# --- DivWrapperProps ---

def test_div_wrapper_props_with_class():
    props = DivWrapperProps(class_name="my-div")
    assert props.to_dict() == {"className": "my-div"}


def test_div_wrapper_props_empty():
    props = DivWrapperProps()
    assert props.to_dict() == {}


# --- SequenceWrapperProps ---

def test_sequence_wrapper_props_with_class():
    props = SequenceWrapperProps(class_name="my-seq")
    assert props.to_dict() == {"className": "my-seq"}


def test_sequence_wrapper_props_empty():
    props = SequenceWrapperProps()
    assert props.to_dict() == {}


# --- TabItemWrapperProps ---

def test_tab_item_wrapper_props_all():
    props = TabItemWrapperProps(label="Tab 1", class_name="tab-cls")
    assert props.to_dict() == {"label": "Tab 1", "className": "tab-cls"}


def test_tab_item_wrapper_props_label_only():
    props = TabItemWrapperProps(label="Details")
    assert props.to_dict() == {"label": "Details"}


def test_tab_item_wrapper_props_empty():
    props = TabItemWrapperProps()
    assert props.to_dict() == {}


# --- TableWrapperProps ---

def test_table_wrapper_props_all():
    search = {"placeholder": "Search items", "searchColumns": ["name", "desc"]}
    header = {"minWidth": "100px"}
    props = TableWrapperProps(label="Items", search=search, header_column_props=header)
    d = props.to_dict()
    assert d == {
        "label": "Items",
        "search": search,
        "headerColumnProps": header,
    }


def test_table_wrapper_props_label_only():
    props = TableWrapperProps(label="Rows")
    assert props.to_dict() == {"label": "Rows"}


def test_table_wrapper_props_empty():
    props = TableWrapperProps()
    assert props.to_dict() == {}
