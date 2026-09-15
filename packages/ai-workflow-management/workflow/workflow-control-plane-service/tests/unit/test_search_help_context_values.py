"""Dependent-lookup ``contextValues`` is built + URL-encoded into queryParams."""

from __future__ import annotations

from app.layer1_domain.auto_form_mapper.helpers import (
    _encode_context_values,
    search_help_select_field,
)


def test_encode_matches_governance_query_param():
    # {"country_1": "AD"} -> the encoded form governance expects
    assert _encode_context_values({"country_1": "AD"}) == "%7B%22country_1%22%3A%20%22AD%22%7D"
    assert _encode_context_values(None) == "%7B%7D"


def test_context_values_flow_into_server_options():
    field = search_help_select_field(
        "city",
        "City",
        template_id="t1",
        model_id="m1",
        bdm_field="cityName",
        dependencies=["country"],
        context_values={"country_1": "AD"},
    )
    server_options = field.field_config.control_props["serverOptions"]
    assert server_options["queryParams"]["contextValues"] == "%7B%22country_1%22%3A%20%22AD%22%7D"
    # templateId is carried as a query param, not appended to the endpoint path
    assert server_options["queryParams"]["templateId"] == "t1"
    assert server_options["endpoint"] == "/canonical-data-models/m1/cdm-fields/cityName/search-help/rows"
    # ``dependencies`` is no longer copied into serverOptions
    assert "dependencies" not in server_options


def test_context_values_omitted_when_absent():
    field = search_help_select_field(
        "city",
        "City",
        template_id="t1",
        model_id="m1",
        bdm_field="cityName",
    )
    server_options = field.field_config.control_props["serverOptions"]
    # without dependent context, contextValues is None rather than an encoded "{}"
    assert server_options["queryParams"]["contextValues"] is None
