"""Credential handling, the same-origin guard, and the download transport."""

from __future__ import annotations

import httpx
import pytest

from app.layer2_application.services import jira_files
from app.layer2_application.services.jira_files import JiraFilesClient, JiraFilesError

_JSON_CRED = {
    "type": "json",
    "config": {"domain": "laidon.atlassian.net", "email": "a@b.co", "apiToken": "tok"},
}


class TestFromCredential:
    def test_json_blob_becomes_basic_auth_on_the_credential_s_own_site(self):
        client = JiraFilesClient.from_credential(_JSON_CRED)
        assert client.base_url == "https://laidon.atlassian.net"
        assert client._auth == ("a@b.co", "tok")

    def test_a_bare_scheme_is_added_but_an_explicit_one_is_kept(self):
        cred = {"type": "json", "config": {"domain": "http://jira.internal", "email": "a", "apiToken": "t"}}
        assert JiraFilesClient.from_credential(cred).base_url == "http://jira.internal"

    def test_bearer_and_api_key_shapes_are_accepted(self):
        bearer = {"type": "bearer", "config": {"domain": "x.atlassian.net", "token": "pat"}}
        api_key = {"type": "api_key", "config": {"domain": "x.atlassian.net", "value": "k"}}
        assert JiraFilesClient.from_credential(bearer)._bearer == "pat"
        assert JiraFilesClient.from_credential(api_key)._bearer == "k"

    def test_no_credential_fails_closed(self):
        with pytest.raises(JiraFilesError) as exc:
            JiraFilesClient.from_credential(None)
        assert exc.value.error_code == "AUTH_ERROR"
        assert "credential_ref" in str(exc.value)

    def test_a_credential_without_a_site_is_refused(self):
        with pytest.raises(JiraFilesError, match="missing the site URL"):
            JiraFilesClient.from_credential({"type": "json", "config": {"email": "a", "apiToken": "t"}})

    def test_an_unusable_credential_type_says_which_one_to_use(self):
        with pytest.raises(JiraFilesError, match="Jira API Token"):
            JiraFilesClient.from_credential({"type": "oauth2", "config": {"domain": "x.atlassian.net"}})


class TestSameOriginGuard:
    """The content URL arrives inside issue JSON — user-editable territory. If it
    were followed blindly, a crafted issue could aim this worker's Jira secret at
    a host of the attacker's choosing."""

    def test_the_credential_s_own_host_is_allowed(self):
        client = JiraFilesClient.from_credential(_JSON_CRED)
        client._assert_same_origin("https://laidon.atlassian.net/rest/api/3/attachment/content/1")

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/rest/api/3/attachment/content/1",
            "http://laidon.atlassian.net/rest/api/3/attachment/content/1",  # scheme downgrade
            "https://laidon.atlassian.net.evil.com/x",
        ],
    )
    def test_any_other_origin_is_refused(self, url):
        client = JiraFilesClient.from_credential(_JSON_CRED)
        with pytest.raises(JiraFilesError) as exc:
            client._assert_same_origin(url)
        assert exc.value.error_code == "VALIDATION_ERROR"

    def test_a_relative_url_is_refused_rather_than_guessed_at(self):
        client = JiraFilesClient.from_credential(_JSON_CRED)
        with pytest.raises(JiraFilesError, match="not absolute"):
            client._assert_same_origin("/rest/api/3/attachment/content/1")

    def test_host_comparison_is_case_insensitive(self):
        client = JiraFilesClient.from_credential(_JSON_CRED)
        client._assert_same_origin("https://LAIDON.Atlassian.NET/rest/api/3/attachment/content/1")


CONTENT_URL = "https://laidon.atlassian.net/rest/api/3/attachment/content/1"


# Captured BEFORE any monkeypatching: the factory below replaces httpx.Client on
# the shared module object, so calling httpx.Client inside it would recurse.
_REAL_CLIENT = httpx.Client


@pytest.fixture
def mock_http(monkeypatch):
    """Route every httpx.Client the module builds through a handler we control."""
    seen = []

    def install(handler):
        def factory(**kw):
            def wrapped(request):
                seen.append(request)
                return handler(request)
            kw.pop("transport", None)
            return _REAL_CLIENT(transport=httpx.MockTransport(wrapped), **kw)
        monkeypatch.setattr(jira_files.httpx, "Client", factory)
        return seen
    return install


class TestDownloadTo:
    def test_asks_for_any_content_type(self, mock_http, tmp_path):
        """Requesting application/octet-stream gets a 406 from Jira: it negotiates
        the attachment's real type (OOXML for an .xlsx). Found on a live run."""
        seen = mock_http(lambda r: httpx.Response(200, content=b"DATA"))
        client = JiraFilesClient.from_credential(_JSON_CRED)
        client.download_to(CONTENT_URL, tmp_path / "a.xlsx", max_bytes=1000)
        assert seen[0].headers["accept"] == "*/*"

    def test_writes_the_bytes_and_reports_size_and_digest(self, mock_http, tmp_path):
        import hashlib
        mock_http(lambda r: httpx.Response(200, content=b"HELLO"))
        client = JiraFilesClient.from_credential(_JSON_CRED)
        meta = client.download_to(CONTENT_URL, tmp_path / "a.xlsx", max_bytes=1000)
        assert (tmp_path / "a.xlsx").read_bytes() == b"HELLO"
        assert meta == {"bytes": 5, "sha256": hashlib.sha256(b"HELLO").hexdigest()}

    def test_a_redirect_to_a_media_host_is_followed_WITHOUT_credentials(self, mock_http, tmp_path):
        """Jira 3xx's to a media host with a signed URL. Re-sending Basic auth
        there would hand this worker's Jira password to a different host."""
        def handler(request):
            if "atlassian.net" in request.url.host:
                return httpx.Response(302, headers={"location": "https://media.example.com/signed?t=x"})
            return httpx.Response(200, content=b"DATA")

        seen = mock_http(handler)
        client = JiraFilesClient.from_credential(_JSON_CRED)
        client.download_to(CONTENT_URL, tmp_path / "a.xlsx", max_bytes=1000)
        media = [r for r in seen if r.url.host == "media.example.com"]
        assert media, "the redirect was not followed"
        assert "authorization" not in media[0].headers

    def test_the_first_hop_still_authenticates(self, mock_http, tmp_path):
        seen = mock_http(lambda r: httpx.Response(200, content=b"DATA"))
        client = JiraFilesClient.from_credential(_JSON_CRED)
        client.download_to(CONTENT_URL, tmp_path / "a.xlsx", max_bytes=1000)
        assert seen[0].headers.get("authorization", "").startswith("Basic ")

    def test_the_size_cap_bites_mid_stream_and_leaves_no_file(self, mock_http, tmp_path):
        mock_http(lambda r: httpx.Response(200, content=b"x" * 5000))
        client = JiraFilesClient.from_credential(_JSON_CRED)
        with pytest.raises(JiraFilesError) as exc:
            client.download_to(CONTENT_URL, tmp_path / "big.xlsx", max_bytes=100)
        assert exc.value.error_code == "VALIDATION_ERROR"
        assert not (tmp_path / "big.xlsx").exists()

    @pytest.mark.parametrize("status,code", [(401, "AUTH_ERROR"), (404, "NOT_FOUND"),
                                             (406, "VALIDATION_ERROR"), (500, "JIRA_ERROR")])
    def test_an_error_status_is_mapped_and_named(self, mock_http, tmp_path, status, code):
        mock_http(lambda r: httpx.Response(status))
        client = JiraFilesClient.from_credential(_JSON_CRED)
        with pytest.raises(JiraFilesError) as exc:
            client.download_to(CONTENT_URL, tmp_path / "a.xlsx", max_bytes=1000)
        assert exc.value.error_code == code
        assert str(status) in str(exc.value)

    def test_a_partial_file_is_never_left_behind(self, mock_http, tmp_path):
        """Neither the destination nor the .part staging file survives a failure."""
        mock_http(lambda r: httpx.Response(404))
        client = JiraFilesClient.from_credential(_JSON_CRED)
        with pytest.raises(JiraFilesError):
            client.download_to(CONTENT_URL, tmp_path / "a.xlsx", max_bytes=1000)
        assert not (tmp_path / "a.xlsx").exists()
        assert not list(tmp_path.glob("*.part"))
