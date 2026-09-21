"""Regression tests: event-mesh token_url resolution on regional BTP landscapes.

Reproduces the tenant-1 (br10) XSUAA 302 bug:
- top-level `credentials` carries only a bare `uaa.url` (no /oauth/token path),
  while the correct token endpoint lives nested in `messaging[*].oa2.tokenendpoint`;
- the /oauth/token auto-append only matched the legacy
  `.authentication.sap.hana.ondemand.com` domain, so it never fired for `.br10.`.

Both defects made the OAuth client POST to the bare XSUAA host -> HTTP 302
`/login?error=invalid_login_request`. These tests fail on the pre-fix code.
"""

from agent_sdk.layer4_frameworks.config.vcap_util import (
    _extract_event_mesh_credentials,
)

REGIONAL_BASE = "https://smdg-ai-tenant-1.authentication.br10.hana.ondemand.com"
REGIONAL_TOKEN = REGIONAL_BASE + "/oauth/token"


def _regional_with_nested_tokenendpoint() -> dict:
    """Real tenant-1 shape: top-level has bare uaa.url; correct tokenendpoint
    only exists nested under messaging[*].oa2."""
    return {
        "namespace": "ns",
        "uaa": {"url": REGIONAL_BASE, "clientid": "cid", "clientsecret": "sec"},
        "messaging": [
            {
                "protocol": ["amqp10ws"],
                "uri": "wss://broker.example",
                "oa2": {
                    "tokenendpoint": REGIONAL_TOKEN,
                    "clientid": "cid",
                    "clientsecret": "sec",
                },
            }
        ],
    }


def _regional_uaa_only() -> dict:
    """Only the bare uaa base URL is present — the token endpoint must be derived
    by appending /oauth/token, region-agnostically."""
    return {
        "namespace": "ns",
        "uaa": {"url": REGIONAL_BASE, "clientid": "cid", "clientsecret": "sec"},
        "messaging": [{"protocol": ["amqp10ws"], "uri": "wss://broker.example"}],
    }


def test_regional_nested_tokenendpoint_not_shadowed_by_uaa_url() -> None:
    creds = _extract_event_mesh_credentials(_regional_with_nested_tokenendpoint())
    assert creds is not None
    assert creds.token_url == REGIONAL_TOKEN
    assert creds.token_url.endswith("/oauth/token")


def test_regional_bare_uaa_url_gets_oauth_token_path() -> None:
    creds = _extract_event_mesh_credentials(_regional_uaa_only())
    assert creds is not None
    assert creds.token_url == REGIONAL_TOKEN
