import json

from worker_sdk.layer1_domain.auto_form_mapper import (
    Schema,
    number_field,
    required,
    required_string,
    select_field,
    switch_field,
    table_array_field,
    text_field,
    textarea_field,
    object_field,
    hidden_field,
    array_field,
    checkbox_field,
    combobox_field,
)


def test_complete_schema_to_json():
    """Build a complete schema and verify serialized JSON structure."""
    schema = Schema(fields=[
        text_field("name", "Full Name", placeholder="Enter name", rules=required_string(2, 100)),
        select_field("role", "Role", [("admin", "Admin"), ("user", "User")], rules=required()),
        switch_field("active", "Active", default=True),
        table_array_field("items", "Line Items", columns=[
            text_field("product", "Product", rules=required()),
            number_field("qty", "Quantity"),
            number_field("price", "Price"),
        ]),
    ])

    json_str = schema.to_json()
    parsed = json.loads(json_str)

    # Top-level structure
    assert "fields" in parsed
    assert len(parsed["fields"]) == 4

    # Field 1: text_field - label in wrapperProps, control with "Control" suffix
    name_field = parsed["fields"][0]
    assert name_field["key"] == "name"
    assert name_field["outputType"] == "string"
    assert name_field["fieldConfig"]["fieldControl"] == "InputControl"
    assert name_field["fieldConfig"]["fieldWrapper"] == "FormItemWrapper"
    assert name_field["fieldConfig"]["wrapperProps"]["label"] == "Full Name"
    assert name_field["fieldConfig"]["wrapperProps"]["required"] is True
    assert name_field["fieldConfig"]["controlProps"]["placeholder"] == "Enter name"
    # Rules in fieldConfig as flat array
    rules = name_field["fieldConfig"]["rules"]
    assert rules[0] == {"method": "required"}
    assert rules[1]["method"] == "min"
    assert rules[2]["method"] == "max"
    # No top-level default or rules
    assert "default" not in name_field
    assert "rules" not in name_field

    # Field 2: select_field
    role_field = parsed["fields"][1]
    assert role_field["key"] == "role"
    assert role_field["fieldConfig"]["fieldControl"] == "SelectControl"
    assert len(role_field["fieldConfig"]["controlProps"]["options"]) == 2
    assert role_field["fieldConfig"]["wrapperProps"]["required"] is True

    # Field 3: switch_field - default in controlProps
    active_field = parsed["fields"][2]
    assert active_field["key"] == "active"
    assert active_field["outputType"] == "boolean"
    assert active_field["fieldConfig"]["fieldControl"] == "SwitchControl"
    assert active_field["fieldConfig"]["controlProps"]["value"] is True
    assert "default" not in active_field

    # Field 4: table_array_field - wrapper suffixed
    items_field = parsed["fields"][3]
    assert items_field["key"] == "items"
    assert items_field["outputType"] == "array"
    assert items_field["fieldConfig"]["fieldWrapper"] == "TableWrapper"
    assert items_field["fieldConfig"]["wrapperProps"]["label"] == "Line Items"
    row_template = items_field["fields"][0]
    assert row_template["key"] == "0"
    assert row_template["fieldConfig"]["fieldWrapper"] == "TableRowWrapper"
    assert len(row_template["fields"]) == 3
    for col in row_template["fields"]:
        assert col["fieldConfig"]["fieldWrapper"] == "TableCellWrapper"


def test_camel_case_keys_in_serialized_output():
    """Verify all keys in JSON output use camelCase."""
    schema = Schema(fields=[
        text_field("test", "Test", rules=required_string()),
    ])
    d = schema.to_dict()
    field_dict = d["fields"][0]

    # Top-level field keys
    assert "outputType" in field_dict
    assert "fieldConfig" in field_dict

    # FieldConfig keys
    fc = field_dict["fieldConfig"]
    assert "fieldControl" in fc
    assert "fieldWrapper" in fc
    assert "wrapperProps" in fc
    assert "rules" in fc


def test_nested_object_schema():
    """Test nested object field serialization."""
    schema = Schema(fields=[
        object_field("address", "Address", children=[
            text_field("street", "Street"),
            text_field("city", "City"),
            object_field("geo", "Coordinates", children=[
                number_field("lat", "Latitude"),
                number_field("lng", "Longitude"),
            ]),
        ]),
    ])

    d = schema.to_dict()
    addr = d["fields"][0]
    assert addr["outputType"] == "object"
    assert addr["fieldConfig"]["wrapperProps"]["label"] == "Address"
    assert len(addr["fields"]) == 3
    geo = addr["fields"][2]
    assert geo["outputType"] == "object"
    assert geo["fieldConfig"]["wrapperProps"]["label"] == "Coordinates"
    assert len(geo["fields"]) == 2
    assert geo["fields"][0]["key"] == "lat"


def test_array_field_schema():
    """Test array field with item template serialization."""
    schema = Schema(fields=[
        array_field("tags", "Tags", item_fields=[
            text_field("value", "Tag Value", rules=required()),
        ]),
    ])

    d = schema.to_dict()
    arr = d["fields"][0]
    assert arr["outputType"] == "array"
    assert arr["fieldConfig"]["fieldWrapper"] == "SequenceWrapper"
    assert arr["fieldConfig"]["wrapperProps"]["label"] == "Tags"
    template = arr["fields"][0]
    assert template["key"] == "0"
    assert template["outputType"] == "object"
    assert template["fieldConfig"]["fieldWrapper"] == "FragmentWrapper"
    assert len(template["fields"]) == 1


def test_hidden_field_in_schema():
    """Test hidden field in a schema - no fieldControl, value in controlProps."""
    schema = Schema(fields=[
        hidden_field("id", default="auto-gen"),
        text_field("name", "Name"),
    ])
    d = schema.to_dict()
    hidden = d["fields"][0]
    assert "fieldControl" not in hidden["fieldConfig"]
    assert hidden["fieldConfig"]["controlProps"]["value"] == "auto-gen"
    assert "default" not in hidden


def test_round_trip_json():
    """Schema -> to_json() -> parse back -> verify structure."""
    schema = Schema(fields=[
        text_field("x", "X", default="hello"),
        number_field("y", "Y", default=42),
        switch_field("z", "Z", default=True),
    ])
    json_str = schema.to_json()
    parsed = json.loads(json_str)

    assert parsed["fields"][0]["fieldConfig"]["controlProps"]["value"] == "hello"
    assert parsed["fields"][1]["fieldConfig"]["controlProps"]["value"] == 42
    assert parsed["fields"][2]["fieldConfig"]["controlProps"]["value"] is True
    assert parsed["fields"][0]["outputType"] == "string"
    assert parsed["fields"][1]["outputType"] == "number"
    assert parsed["fields"][2]["outputType"] == "boolean"


def test_new_controls_in_schema():
    """Test new control types serialize correctly."""
    schema = Schema(fields=[
        checkbox_field("agree", "I Agree", rules=required()),
        combobox_field("country", "Country", [("us", "USA"), ("uk", "UK")]),
    ])
    d = schema.to_dict()
    assert d["fields"][0]["fieldConfig"]["fieldControl"] == "CheckBoxControl"
    assert d["fields"][0]["fieldConfig"]["wrapperProps"]["required"] is True
    assert d["fields"][1]["fieldConfig"]["fieldControl"] == "ComboBoxControl"
    assert len(d["fields"][1]["fieldConfig"]["controlProps"]["options"]) == 2
