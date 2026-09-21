from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Units under test: _import_file_to_node_impl + check_table_field_key
# (layer2 feature import_file_to_node). Collaborators: AppSettingsAdapter
# (sync) and httpx.AsyncClient (async ctx mgr with async .get/.patch).
# Grounded in app/layer2_application/features/import_file_to_node.py.


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient used as `async with ... as client`.

    responses: list of MagicMock responses handed out per get/patch call, in call order.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.get = AsyncMock(side_effect=self._next)
        self.patch = AsyncMock(side_effect=self._next)

    async def _next(self, *args, **kwargs):
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _resp(status_code, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status_code
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=json_body or {})
    r.text = text
    return r


def _patch(monkeypatch, responses):
    from app.layer2_application.features import import_file_to_node as mod

    monkeypatch.setattr(
        mod,
        "AppSettingsAdapter",
        lambda: SimpleNamespace(WORKFLOW_API_BASE_URL="https://wf.example/"),
    )
    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: client)
    return mod, client


# --- happy path: table field detected, then PATCH succeeds ----------------


@pytest.mark.asyncio
async def test_import_happy_with_table_field_key(monkeypatch):
    # 1st call = check_table_field_key GET (200 with TableWrapper); 2nd = PATCH 200.
    input_schema = [
        {"key": "myTable", "fieldConfig": {"fieldWrapper": "TableWrapper"}}
    ]
    mod, client = _patch(
        monkeypatch,
        [
            _resp(200, {"data": {"input_schema": input_schema}}),
            _resp(200),
        ],
    )
    result = await mod._import_file_to_node_impl("run-1", "node-1", "file-9")
    assert result == {
        "message": "Upload file successfully assigned to node input",
        "file_id": "file-9",
    }
    # source: target_field added to body when table_field_key present
    patch_body = client.patch.call_args.kwargs["json"]
    assert patch_body == {"auto_rerun": False, "file_id": "file-9", "target_field": "myTable"}


# --- edge: no table field -> body omits target_field ----------------------


@pytest.mark.asyncio
async def test_import_no_table_field_omits_target_field(monkeypatch):
    # check_table_field_key GET returns 200 but non-TableWrapper -> None.
    input_schema = [{"key": "plain", "fieldConfig": {"fieldWrapper": "TextWrapper"}}]
    mod, client = _patch(
        monkeypatch,
        [
            _resp(200, {"data": {"input_schema": input_schema}}),
            _resp(200),
        ],
    )
    result = await mod._import_file_to_node_impl("run-1", "node-1", "file-9")
    assert result["file_id"] == "file-9"
    patch_body = client.patch.call_args.kwargs["json"]
    assert "target_field" not in patch_body
    assert patch_body == {"auto_rerun": False, "file_id": "file-9"}


# --- boundary: empty input_schema -> table_field_key None -----------------


@pytest.mark.asyncio
async def test_check_table_field_key_empty_schema_returns_none(monkeypatch):
    mod, _client = _patch(monkeypatch, [_resp(200, {"data": {"input_schema": []}})])
    key = await mod.check_table_field_key("run-1", "node-1")
    # source: `if not input_schema: return None`
    assert key is None


@pytest.mark.asyncio
async def test_check_table_field_key_non_200_returns_none(monkeypatch):
    mod, _client = _patch(monkeypatch, [_resp(500, text="boom")])
    key = await mod.check_table_field_key("run-1", "node-1")
    # source: falls through the 200 block, logs error, returns None
    assert key is None


# --- failure path: node input PATCH returns non-200 -----------------------


@pytest.mark.asyncio
async def test_import_patch_non_200_returns_request_failed(monkeypatch):
    # check_table_field_key GET 200 empty schema -> None; PATCH 409 -> error dict.
    mod, _client = _patch(
        monkeypatch,
        [
            _resp(200, {"data": {"input_schema": []}}),
            _resp(409, text="conflict"),
        ],
    )
    result = await mod._import_file_to_node_impl("run-1", "node-1", "file-9")
    # source else-branch of the PATCH: {"error": "Request failed", "status_code", "response"}
    assert result == {
        "error": "Request failed",
        "status_code": 409,
        "response": "conflict",
    }


@pytest.mark.asyncio
async def test_import_file_to_node_tool_wrapper_delegates(monkeypatch):
    # Exercises the @tool wrapper (import_file_to_node) -> _impl path.
    mod, _client = _patch(
        monkeypatch,
        [
            _resp(200, {"data": {"input_schema": []}}),
            _resp(200),
        ],
    )
    result = await mod.import_file_to_node.ainvoke(
        {"run_id": "r", "node_id": "n", "uploaded_file_id": "f-9"}
    )
    assert result == {
        "message": "Upload file successfully assigned to node input",
        "file_id": "f-9",
    }
