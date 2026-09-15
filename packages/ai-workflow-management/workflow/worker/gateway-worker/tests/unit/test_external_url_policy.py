"""Unit tests for ExternalUrlPolicy SSRF + allowlist."""
from __future__ import annotations

import pytest

from app.layer1_domain.value_objects.external_url_policy import (
    ExternalUrlPolicy,
    HostNotAllowed,
    PrivateIpBlocked,
    SchemeNotAllowed,
)


def _policy(*hosts, schemes=("https",), resolver=None):
    return ExternalUrlPolicy(
        allowed_hosts=tuple(hosts),
        allowed_schemes=schemes,
        resolver=resolver or (lambda h: "8.8.8.8"),
    )


@pytest.mark.unit
def test_check_accepts_exact_host():
    p = _policy("api.partner.com")
    assert p.check("https://api.partner.com/x") == "api.partner.com"


@pytest.mark.unit
def test_check_accepts_one_level_wildcard():
    p = _policy("*.partner.com")
    assert p.check("https://api.partner.com/x") == "api.partner.com"


@pytest.mark.unit
def test_check_rejects_two_level_against_one_level_wildcard():
    p = _policy("*.partner.com")
    with pytest.raises(HostNotAllowed):
        p.check("https://a.b.partner.com/x")


@pytest.mark.unit
def test_check_rejects_apex_against_wildcard():
    p = _policy("*.partner.com")
    with pytest.raises(HostNotAllowed):
        p.check("https://partner.com/x")


@pytest.mark.unit
def test_check_rejects_unknown_host():
    p = _policy("api.partner.com")
    with pytest.raises(HostNotAllowed):
        p.check("https://api.intruder.com/x")


@pytest.mark.unit
def test_check_rejects_non_https_scheme_by_default():
    p = _policy("api.partner.com")
    with pytest.raises(SchemeNotAllowed):
        p.check("http://api.partner.com/x")


@pytest.mark.unit
def test_check_accepts_http_when_explicitly_allowed():
    p = _policy("api.partner.com", schemes=("https", "http"))
    assert p.check("http://api.partner.com/x") == "api.partner.com"


@pytest.mark.unit
def test_check_blocks_private_ip_after_resolve():
    p = _policy("api.partner.com", resolver=lambda h: "10.0.0.1")
    with pytest.raises(PrivateIpBlocked):
        p.check("https://api.partner.com/x")


@pytest.mark.unit
def test_check_blocks_loopback_ip():
    p = _policy("api.partner.com", resolver=lambda h: "127.0.0.1")
    with pytest.raises(PrivateIpBlocked):
        p.check("https://api.partner.com/x")


@pytest.mark.unit
def test_check_blocks_link_local_ip():
    p = _policy("api.partner.com", resolver=lambda h: "169.254.169.254")  # AWS IMDS!
    with pytest.raises(PrivateIpBlocked):
        p.check("https://api.partner.com/x")


@pytest.mark.unit
def test_check_blocks_when_dns_fails():
    def bad_resolver(h):
        raise OSError("nxdomain")

    p = _policy("api.partner.com", resolver=bad_resolver)
    with pytest.raises(HostNotAllowed, match="DNS"):
        p.check("https://api.partner.com/x")


@pytest.mark.unit
def test_check_rejects_url_without_host():
    p = _policy("api.partner.com")
    with pytest.raises(HostNotAllowed):
        p.check("https:///foo")
