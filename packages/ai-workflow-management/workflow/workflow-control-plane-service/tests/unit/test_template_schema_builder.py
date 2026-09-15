"""Unit tests for the Template Data Editor schema builder + field mapping.

Mirrors the structure asserted in
``tests/integration/test_input_node_auto_provision_odata.py`` (OdataWrapper →
row object → cells) but focuses on the value-help / dependent-lookup wiring.
"""

from __future__ import annotations

from app.layer1_domain.template_schema import (
    TemplateFieldConfig,
    build_template_data_edit_schema,
)
from app.layer1_domain.template_schema.builder import TABLE_KEY

MODEL = "25fa8294"
TEMPLATE_ID = "019ed482-tmpl"


def _fields() -> list[TemplateFieldConfig]:
    # Mirrors sample/sample_field_config.csv: country (direct lookup) +
    # cityName (dependent on country) + streetName (plain).
    #
    # The city deliberately uses a column alias ("City") that differs from its
    # API field name ("cityName"), mirroring real governance data: the cell key
    # binds to the alias, but valueKey/labelKey and ${rowData.*} read the field.
    return [
        TemplateFieldConfig(
            field="country", alias="Country", cdm_id="country_1",
            value_help=True, binding_kind="DIRECT_LOOKUP", order=4, required=True,
        ),
        TemplateFieldConfig(
            field="streetName", alias="streetName", cdm_id="street_1",
            value_help=False, order=5,
        ),
        TemplateFieldConfig(
            field="cityName", alias="City", cdm_id="city_1",
            value_help=True, binding_kind="DEPENDENT_LOOKUP",
            dependency_fields=("country_1",), order=6,
        ),
    ]


def _build() -> dict:
    schema = build_template_data_edit_schema(
        _fields(), template_id=TEMPLATE_ID, model_id=MODEL, page_size=100,
        label="Locations",
    )
    assert len(schema) == 1
    return schema[0].to_dict()


def _cells(table: dict) -> dict[str, dict]:
    row = table["fields"][0]
    return {c["key"]: c for c in row["fields"]}


def test_root_is_odata_array_table():
    table = _build()
    assert table["key"] == TABLE_KEY
    assert table["outputType"] == "array"
    assert table["fieldConfig"]["fieldWrapper"] == "OdataWrapper"
    # editable (not readonly) → add/edit/delete toggles
    wp = table["fieldConfig"]["wrapperProps"]
    assert wp["isAdd"] and wp["isEdit"] and wp["isDelete"]


def test_columns_ordered_by_order_field():
    # Cell keys are the alias-based column_key (the requester file's headers):
    # country → "Country", street → "streetName", city → "City" (alias != field).
    cells = list(_cells(_build()).keys())
    assert cells == ["Country", "streetName", "City"]


def test_plain_field_is_input_control():
    cells = _cells(_build())
    street = cells["streetName"]["fieldConfig"]
    assert street["fieldControl"] == "InputControl"
    assert "serverOptions" not in (street.get("controlProps") or {})


def test_value_help_field_points_at_search_help_endpoint():
    # Cell key is the alias ("Country"); valueKey/labelKey are the result key.
    country = _cells(_build())["Country"]["fieldConfig"]
    assert country["fieldControl"] == "SelectTableControl"
    cp = country["controlProps"]
    # value-help rows are keyed by the API field name (here field == "country").
    assert cp["valueKey"] == "country"
    assert cp["labelKey"] == "country"
    so = cp["serverOptions"]
    # governance search-help service; endpoint is relative to the service basePath.
    assert so["service"] == "governance"
    # templateId is a query param, not appended to the endpoint path.
    assert so["endpoint"] == (
        f"/canonical-data-models/{MODEL}/cdm-fields/country_1/search-help/rows"
    )
    assert so["queryParams"]["templateId"] == TEMPLATE_ID
    # response shape: { data: [...], totalCount: N }
    assert so["responseMapping"] == {"dataPath": "data", "totalPath": "count"}


def test_every_value_help_field_has_select_action():
    # Cell keyed by alias ("Country"/"City"); action name stays field-based
    # (so the city cell "City" fires "on_select_cityName").
    cells = _cells(_build())
    assert cells["Country"]["fieldConfig"]["controlProps"]["selectAction"] == "on_select_country"
    assert cells["City"]["fieldConfig"]["controlProps"]["selectAction"] == "on_select_cityName"


def test_value_help_field_value_and_label_keys_use_api_field_name():
    # City cell binds to alias "City", but its rows are keyed by the API field
    # name "cityName" — so valueKey/labelKey must be "cityName", not "City".
    city = _cells(_build())["City"]["fieldConfig"]["controlProps"]
    assert city["valueKey"] == "cityName"
    assert city["labelKey"] == "cityName"


def test_dependent_lookup_carries_dependencies_and_context_values():
    city = _cells(_build())["City"]["fieldConfig"]["controlProps"]
    so = city["serverOptions"]
    assert "/cdm-fields/city_1/search-help/rows" in so["endpoint"]
    # context key is the parent CDM id, value is the parent column key (alias);
    # carried as a URL-encoded JSON string in queryParams (governance contract).
    # decodes to {"country_1": "Country"}
    assert so["queryParams"]["contextValues"] == "%7B%22country_1%22%3A%20%22Country%22%7D"


def test_select_action_fills_own_value_from_result_and_retriggers_dependents():
    actions = {a["action"]: a for a in _build()["fieldConfig"]["wrapperProps"]["actions"]}
    # Parent action writes its own value from the picked row + re-triggers the
    # dependent child so its search-help re-queries with the new context.
    # LHS keys are alias-based column keys ("Country"/"City"); each RHS reads the
    # picked row by API field name ("country"/"cityName").
    country_data = actions["on_select_country"]["steps"][0]["data"]
    assert country_data["Country"] == "${rowData.country}"
    # dependent-clear: child column key "City", but RHS uses the child field name.
    assert country_data["City"] == "${rowData.cityName}"
    # Child action writes its own value: column key "City", read by field "cityName".
    city_data = actions["on_select_cityName"]["steps"][0]["data"]
    assert city_data["City"] == "${rowData.cityName}"


def test_required_field_has_required_rule():
    country = _cells(_build())["Country"]["fieldConfig"]
    methods = [r["method"] for r in country.get("rules", [])]
    assert "required" in methods


def test_readonly_renders_plain_inputs_without_action_toggles():
    schema = build_template_data_edit_schema(
        _fields(), template_id=TEMPLATE_ID, model_id=MODEL, readonly=True,
    )
    wp = schema[0].to_dict()["fieldConfig"]["wrapperProps"]
    assert wp.get("readonly") is True
    assert "isAdd" not in wp


def test_result_key_derives_from_field_and_respects_override():
    # Flat rows are keyed by the API field name, not the CDM id.
    assert TemplateFieldConfig(field="cityName", cdm_id="city_1").result_key == "cityName"
    assert TemplateFieldConfig(field="country", cdm_id="country_1").result_key == "country"
    # explicit override wins over the field name
    assert TemplateFieldConfig(
        field="cityName", cdm_id="city_1", payload_field="city"
    ).result_key == "city"
    # last resort: no field → suffix-stripped CDM id
    assert TemplateFieldConfig(field="", cdm_id="city_1").result_key == "city"


def test_from_api_maps_csv_style_and_camelcase_keys():
    # spreadsheet-style headers (sample_field_config.csv)
    csv_style = TemplateFieldConfig.from_api({
        "Field": "cityName", "Alias": "cityName", "CDM ID": "city_1",
        "Value Help": "true", "Binding Kind": "DEPENDENT_LOOKUP",
        "Dependency Fields": "country_1", "Order": "6",
    })
    assert csv_style.field == "cityName"
    assert csv_style.cdm_id == "city_1"
    assert csv_style.value_help is True
    assert csv_style.dependency_fields == ("country_1",)
    assert csv_style.order == 6
    assert csv_style.is_lookup is True

    # camelCase API keys
    camel = TemplateFieldConfig.from_api({
        "field": "country", "alias": "Country", "cdmId": "country_1",
        "valueHelp": True, "dependencyFields": ["a_1", "b_1"],
    })
    assert camel.cdm_id == "country_1"
    assert camel.dependency_fields == ("a_1", "b_1")

    # plain field, no value help
    plain = TemplateFieldConfig.from_api({"Field": "language", "Value Help": "false"})
    assert plain.is_lookup is False
