"""Unit specs for layer1 domain module `section_definition`.

Target: app/layer1_domain/entities/section_definition.py
Units:
  - SectionDefinitions.normalize   (pure)
  - validate_parts                 (pure)
  - format_section_definitions     (pure)
  - fetch_section_definitions      (async; collaborators = AppSettingsAdapter + httpx.AsyncClient)

For fetch_section_definitions the async collaborator is httpx.AsyncClient, so it
is mocked with AsyncMock (mock-kind matches: the client's .get and
response.raise_for_status paths are async / the client is an async ctx manager).
Every expected value/exception is grounded in the source.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.layer1_domain.entities.section_definition import (
    SectionDefinition,
    SectionDefinitions,
    fetch_section_definitions,
    format_section_definitions,
    validate_parts,
)


# ---------------------------------------------------------------------------
# SectionDefinitions.normalize
# ---------------------------------------------------------------------------
class TestNormalize:
    def test_happy_maps_section_and_fields(self):
        out = SectionDefinitions.normalize(
            [{"section": "Supply Chain", "fields": ["MRP type", 42]}]
        )
        assert len(out) == 1
        assert out[0].section == "Supply Chain"
        # fields coerced to str (grounded: [str(field) for field in fields])
        assert out[0].fields == ["MRP type", "42"]

    def test_edge_missing_section_name_is_skipped(self):
        out = SectionDefinitions.normalize(
            [{"fields": ["x"]}, {"section": "Finance", "fields": []}]
        )
        assert [s.section for s in out] == ["Finance"]

    def test_edge_non_list_fields_defaults_to_empty(self):
        out = SectionDefinitions.normalize([{"section": "A", "fields": "notalist"}])
        assert out[0].fields == []

    def test_boundary_empty_input_returns_empty_list(self):
        assert SectionDefinitions.normalize([]) == []

    def test_boundary_missing_fields_key_defaults_empty(self):
        out = SectionDefinitions.normalize([{"section": "A"}])
        assert out[0].fields == []


# ---------------------------------------------------------------------------
# validate_parts
# ---------------------------------------------------------------------------
class TestValidateParts:
    def _sections(self):
        return [SectionDefinition(section="Finance", fields=["valuation class"])]

    def test_happy_matching_section_gets_fields_attached(self):
        import json

        out = validate_parts('[{"section":"Finance"}]', self._sections())
        parsed = json.loads(out)
        assert parsed[0]["section"] == "Finance"
        # fields replaced with the section_def's fields (grounded: parts["fields"] = section_def.fields)
        assert parsed[0]["fields"] == ["valuation class"]

    def test_invalid_json_string_raises_value_error(self):
        with pytest.raises(ValueError, match="parts must be valid List string"):
            validate_parts("not-json", self._sections())

    def test_failure_unknown_section_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid section"):
            validate_parts('[{"section":"Nope"}]', self._sections())

    def test_boundary_no_allowed_sections_raises(self):
        with pytest.raises(ValueError, match="allowed section definitions are required"):
            validate_parts('[{"section":"Finance"}]', [])

    def test_edge_empty_parts_list_returns_empty_json_array(self):
        # edge: empty list -> loop body never runs -> "[]" (but allowed_sections must be truthy)
        assert validate_parts("[]", self._sections()) == "[]"


# ---------------------------------------------------------------------------
# format_section_definitions
# ---------------------------------------------------------------------------
class TestFormatSectionDefinitions:
    def test_happy_formats_lines(self):
        out = format_section_definitions(
            [SectionDefinition(section="A", fields=[]), SectionDefinition(section="B", fields=[])]
        )
        assert out == "- section=A\n- section=B"

    def test_boundary_none_returns_empty_string(self):
        assert format_section_definitions(None) == ""

    def test_boundary_empty_list_returns_empty_string(self):
        assert format_section_definitions([]) == ""


# ---------------------------------------------------------------------------
# fetch_section_definitions  (async)
# ---------------------------------------------------------------------------
class TestFetchSectionDefinitions:
    def _mock_client_cm(self, response):
        """Build an object usable as `async with httpx.AsyncClient(...) as client`."""
        client = MagicMock()
        client.get = AsyncMock(return_value=response)  # async .get
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm, client

    @pytest.mark.asyncio
    async def test_happy_returns_normalized_sections(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()  # sync in httpx
        response.json = MagicMock(
            return_value={"data": [{"section": "Finance", "fields": ["vc"]}]}
        )
        cm, client = self._mock_client_cm(response)
        with patch(
            "app.layer1_domain.entities.section_definition.httpx.AsyncClient",
            return_value=cm,
        ):
            out = await fetch_section_definitions("tmpl-1")
        assert [s.section for s in out] == ["Finance"]
        client.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_empty_template_id_raises(self):
        # invalid input: falsy template_id -> ValueError before any HTTP call
        with pytest.raises(ValueError, match="template_id is required"):
            await fetch_section_definitions("")

    @pytest.mark.asyncio
    async def test_edge_payload_without_data_yields_empty_list(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={})  # no "data" key -> [] default
        cm, _ = self._mock_client_cm(response)
        with patch(
            "app.layer1_domain.entities.section_definition.httpx.AsyncClient",
            return_value=cm,
        ):
            out = await fetch_section_definitions("tmpl-1")
        assert out == []

    @pytest.mark.asyncio
    async def test_failure_data_not_a_list_raises(self):
        # failure path: payload["data"] is a dict, not a list -> ValueError
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"data": {"not": "a list"}})
        cm, _ = self._mock_client_cm(response)
        with patch(
            "app.layer1_domain.entities.section_definition.httpx.AsyncClient",
            return_value=cm,
        ):
            with pytest.raises(ValueError, match="must contain a list"):
                await fetch_section_definitions("tmpl-1")

    @pytest.mark.asyncio
    async def test_failure_http_error_propagates(self):
        # failure path: raise_for_status raises -> propagates out of fetch
        response = MagicMock()
        response.raise_for_status = MagicMock(side_effect=RuntimeError("boom 500"))
        cm, _ = self._mock_client_cm(response)
        with patch(
            "app.layer1_domain.entities.section_definition.httpx.AsyncClient",
            return_value=cm,
        ):
            with pytest.raises(RuntimeError, match="boom 500"):
                await fetch_section_definitions("tmpl-1")
