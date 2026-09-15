"""Unit tests for HttpGovernanceClient.get_template_fields (mocked transport)."""

from __future__ import annotations

import pytest

from app.layer4_frameworks.clients.http_governance_client import HttpGovernanceClient


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                "error", request=None, response=None,  # type: ignore[arg-type]
            )

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_url = None

    async def get(self, url, *args, **kwargs):
        self.last_url = url
        return self._response


def _client_with(response: _FakeResponse) -> HttpGovernanceClient:
    client = HttpGovernanceClient("https://gov.example.com/")
    client._client = _FakeAsyncClient(response)  # type: ignore[assignment]
    return client


@pytest.mark.asyncio
async def test_get_template_fields_maps_envelope_data():
    payload = {"success": True, "data": [
        {"Field": "country", "Alias": "Country", "CDM ID": "country_1",
         "Value Help": "true", "Binding Kind": "DIRECT_LOOKUP", "Order": "4"},
        {"Field": "cityName", "CDM ID": "city_1", "Value Help": "true",
         "Binding Kind": "DEPENDENT_LOOKUP", "Dependency Fields": "country_1", "Order": "6"},
    ]}
    client = _client_with(_FakeResponse(200, payload))

    fields = await client.get_template_fields("tmpl-123")

    assert client._client.last_url == (  # type: ignore[attr-defined]
        "https://gov.example.com/api/v1/templates/tmpl-123/fields"
    )
    assert [f.field for f in fields] == ["country", "cityName"]
    assert fields[0].cdm_id == "country_1" and fields[0].is_lookup
    assert fields[1].dependency_fields == ("country_1",)


@pytest.mark.asyncio
async def test_get_template_fields_accepts_bare_list():
    payload = [{"field": "language", "valueHelp": False}]
    client = _client_with(_FakeResponse(200, payload))
    fields = await client.get_template_fields("t")
    assert len(fields) == 1 and fields[0].is_lookup is False


@pytest.mark.asyncio
async def test_get_template_fields_404_raises_not_found():
    client = _client_with(_FakeResponse(404, {"error": "nope"}))
    with pytest.raises(FileNotFoundError):
        await client.get_template_fields("missing")
