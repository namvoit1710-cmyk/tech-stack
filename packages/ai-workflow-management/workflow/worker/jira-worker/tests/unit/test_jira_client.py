"""Unit tests for JiraClient — credential parsing, ADF, transport, operations."""

import pytest

from app.layer2_application.services import jira_client as jc
from app.layer2_application.services.jira_client import JiraClient, JiraError, text_to_adf


# -- text_to_adf --------------------------------------------------------------


def test_text_to_adf_plain_text():
    doc = text_to_adf("Hello world")
    assert doc["type"] == "doc"
    assert doc["version"] == 1
    assert doc["content"][0]["content"][0]["text"] == "Hello world"


def test_text_to_adf_multiline_and_blank():
    doc = text_to_adf("line1\n\nline3")
    assert len(doc["content"]) == 3
    # blank middle line becomes an empty paragraph (no content key)
    assert "content" not in doc["content"][1]


def test_text_to_adf_none_and_empty():
    assert text_to_adf(None) is None
    assert text_to_adf("   ") is None


def test_text_to_adf_dict_passthrough():
    adf = {"type": "doc", "version": 1, "content": []}
    assert text_to_adf(adf) is adf


def test_text_to_adf_json_string_that_is_adf():
    raw = '{"type": "doc", "version": 1, "content": []}'
    assert text_to_adf(raw) == {"type": "doc", "version": 1, "content": []}


# -- from_credential ----------------------------------------------------------


def test_from_credential_json_builds_basic_auth():
    cred = {"type": "json", "config": {"domain": "https://laidon.atlassian.net", "email": "e@x.com", "apiToken": "tok"}}
    client = JiraClient.from_credential(cred)
    assert client.base_url == "https://laidon.atlassian.net"
    assert client._auth == ("e@x.com", "tok")


def test_from_credential_adds_scheme_when_missing():
    cred = {"type": "json", "config": {"domain": "laidon.atlassian.net", "email": "e@x.com", "apiToken": "tok"}}
    client = JiraClient.from_credential(cred)
    assert client.base_url == "https://laidon.atlassian.net"


def test_from_credential_bearer_pat():
    cred = {"type": "bearer", "config": {"domain": "https://jira.internal", "token": "pat-1"}}
    client = JiraClient.from_credential(cred)
    assert client._bearer == "pat-1"
    assert client._auth is None


def test_from_credential_missing_domain_raises_auth_error():
    with pytest.raises(JiraError) as ei:
        JiraClient.from_credential({"type": "json", "config": {"email": "e@x.com", "apiToken": "t"}})
    assert ei.value.error_code == "AUTH_ERROR"


def test_from_credential_none_raises():
    with pytest.raises(JiraError):
        JiraClient.from_credential(None)


# -- transport (_request) via a fake httpx.Client -----------------------------


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text_body=""):
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if status_code == 204 else b"{}"
        self.text = text_body

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    def __init__(self, resp, sink):
        self._resp = resp
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, params=None, json=None, files=None, headers=None, auth=None):
        self._sink.update(method=method, url=url, params=params, json=json, files=files, headers=headers, auth=auth)
        return self._resp


def _patch_httpx(monkeypatch, resp):
    sink: dict = {}
    monkeypatch.setattr(jc.httpx, "Client", lambda timeout=None: _FakeClient(resp, sink))
    return sink


def test_request_sends_basic_auth_and_builds_url(monkeypatch):
    sink = _patch_httpx(monkeypatch, _FakeResp(200, {"accountId": "a1"}))
    client = JiraClient("https://laidon.atlassian.net", auth=("e@x.com", "tok"))
    out = client.myself()
    assert out == {"accountId": "a1"}
    assert sink["url"] == "https://laidon.atlassian.net/rest/api/3/myself"
    assert sink["auth"] == ("e@x.com", "tok")
    assert sink["headers"]["Accept"] == "application/json"


def test_request_bearer_sets_authorization_header(monkeypatch):
    sink = _patch_httpx(monkeypatch, _FakeResp(200, {"ok": True}))
    client = JiraClient("https://jira.internal", bearer="pat-1")
    client.myself()
    assert sink["headers"]["Authorization"] == "Bearer pat-1"
    assert sink["auth"] is None


def test_request_204_returns_none(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResp(204))
    client = JiraClient("https://x", auth=("e", "t"))
    assert client.update_issue("SA-1", {"summary": "s"}) is None


def test_request_404_raises_not_found(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResp(404, {"errorMessages": ["Issue does not exist"]}))
    client = JiraClient("https://x", auth=("e", "t"))
    with pytest.raises(JiraError) as ei:
        client.get_issue("SA-999")
    assert ei.value.error_code == "NOT_FOUND"
    assert "Issue does not exist" in str(ei.value)


def test_request_401_raises_auth_error(monkeypatch):
    _patch_httpx(monkeypatch, _FakeResp(401, {"errorMessages": ["Unauthorized"]}))
    client = JiraClient("https://x", auth=("e", "t"))
    with pytest.raises(JiraError) as ei:
        client.myself()
    assert ei.value.error_code == "AUTH_ERROR"
    assert ei.value.status_code == 401


# -- operation methods build the right path + body (via rec fixture) ----------


def test_get_issue_path_and_fields(rec):
    client = JiraClient("https://x", auth=("e", "t"))
    client.get_issue("SA-1", fields="summary,status")
    call = rec.last()
    assert call["method"] == "GET"
    assert call["path"] == "/issue/SA-1"
    assert call["params"] == {"fields": "summary,status"}


def test_search_jql_body(rec):
    client = JiraClient("https://x", auth=("e", "t"))
    client.search_jql("project = SA", ["summary"], 25, 0)
    call = rec.last()
    assert call["path"] == "/search/jql"
    assert call["json"] == {"jql": "project = SA", "maxResults": 25, "startAt": 0, "fields": ["summary"]}


def test_create_issue_wraps_fields(rec):
    client = JiraClient("https://x", auth=("e", "t"))
    client.create_issue({"summary": "s"})
    assert rec.last()["json"] == {"fields": {"summary": "s"}}


def test_transition_issue_body(rec):
    client = JiraClient("https://x", auth=("e", "t"))
    client.transition_issue("SA-1", "31", {"resolution": {"name": "Done"}})
    call = rec.last()
    assert call["path"] == "/issue/SA-1/transitions"
    assert call["json"] == {"transition": {"id": "31"}, "fields": {"resolution": {"name": "Done"}}}


def test_link_issues_body(rec):
    client = JiraClient("https://x", auth=("e", "t"))
    client.link_issues("SA-1", "SA-2", "Blocks")
    assert rec.last()["json"] == {
        "type": {"name": "Blocks"},
        "inwardIssue": {"key": "SA-1"},
        "outwardIssue": {"key": "SA-2"},
    }


def test_add_attachment_sets_xsrf_header(rec):
    client = JiraClient("https://x", auth=("e", "t"))
    client.add_attachment("SA-1", "f.txt", b"data")
    call = rec.last()
    assert call["path"] == "/issue/SA-1/attachments"
    assert call["headers"]["X-Atlassian-Token"] == "no-check"
    assert call["files"]["file"][0] == "f.txt"
