"""Unit tests for HttpIntegrationHubClient search-help query + flatten."""

from __future__ import annotations

import pytest

from app.layer4_frameworks.clients.http_integration_hub_client import (
    HttpIntegrationHubClient,
    flatten_search_help_response,
)

# Trimmed sample of the live search-help response.
SAMPLE_RESPONSE = {
    "sourceId": "da389ec6",
    "page": 1,
    "pageSize": 100,
    "hasMore": True,
    "totalCount": 32086,
    "items": [
        {
            "rowKey": "https://s42023/.../MDADDRESSID(addressNumber='1')",
            "rowSeq": 1,
            "payload": {"addressNumber": "1", "city": "", "country": "VN", "name": "Laidon"},
            "entityType": "cds_xsimplexmdg_f4.MDADDRESSIDType",
        },
        {
            "rowKey": "https://s42023/.../MDADDRESSID(addressNumber='109')",
            "rowSeq": 2,
            "payload": {"addressNumber": "109", "city": "LA", "country": "US", "name": "Nissan"},
            "entityType": "cds_xsimplexmdg_f4.MDADDRESSIDType",
        },
    ],
}


def test_flatten_spreads_payload_with_row_id():
    out = flatten_search_help_response(SAMPLE_RESPONSE)
    assert out["file_id"] == ""
    assert out["total_count"] == 32086
    assert out["data"] == [
        {"__row_id": "1", "addressNumber": "1", "city": "", "country": "VN", "name": "Laidon"},
        {"__row_id": "2", "addressNumber": "109", "city": "LA", "country": "US", "name": "Nissan"},
    ]
    # rowKey / metadata are dropped — only payload fields + __row_id remain.
    assert "rowKey" not in out["data"][0]
    assert "entityType" not in out["data"][0]


def test_flatten_passes_file_id_through():
    out = flatten_search_help_response(SAMPLE_RESPONSE, file_id="file_abc")
    assert out["file_id"] == "file_abc"


def test_flatten_row_id_falls_back_to_index_when_no_row_seq():
    resp = {"items": [{"payload": {"x": "1"}}, {"payload": {"x": "2"}}]}
    out = flatten_search_help_response(resp)
    assert [r["__row_id"] for r in out["data"]] == ["1", "2"]


def test_flatten_total_count_falls_back_to_len_when_missing():
    resp = {"items": [{"rowSeq": 1, "payload": {"x": "1"}}]}
    out = flatten_search_help_response(resp)
    assert out["total_count"] == 1


def test_flatten_handles_empty_and_malformed_items():
    assert flatten_search_help_response({}) == {"file_id": "", "total_count": 0, "data": []}
    # non-dict items are skipped; missing payload yields a row with just __row_id.
    out = flatten_search_help_response({"items": ["nope", {"rowSeq": 5}]})
    assert out["data"] == [{"__row_id": "5"}]


@pytest.mark.asyncio
async def test_query_search_help_data_fetches_then_flattens(monkeypatch):
    client = HttpIntegrationHubClient("https://hub.example.test/")

    captured = {}

    async def _fake_fetch(**kwargs):
        captured.update(kwargs)
        return SAMPLE_RESPONSE

    monkeypatch.setattr(client, "fetch_search_help_rows", _fake_fetch)

    out = await client.query_search_help_data(
        model_id="MID", bdm_field="city_1", page=2, page_size=50,
        context_values={"country_1": "VN"}, file_id="file_x",
    )

    # delegates to fetch_search_help_rows with the same args
    assert captured == {
        "model_id": "MID", "bdm_field": "city_1", "page": 2,
        "page_size": 50, "context_values": {"country_1": "VN"},
    }
    # returns the flattened envelope
    assert out["file_id"] == "file_x"
    assert out["total_count"] == 32086
    assert out["data"][0]["country"] == "VN"
    assert out["data"][1]["__row_id"] == "2"
