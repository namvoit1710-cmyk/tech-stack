"""Domain rule: is a function's target valid for its parent app's kind? (P4)

A ``GatewayFunction`` is a separate aggregate and cannot hold its parent app's
``kind`` / ``allowed_hosts``, so the kind-aware path rule lives here as a domain
validator (mirroring ``validate_auth_config``). The create/update function use
cases load the parent app anyway and call this; the gateway-worker's
``ExternalUrlPolicy`` remains the hard runtime SSRF gate (author-time + runtime).

Rules:
- INTERNAL ⇒ ``path_template`` is a relative path (leading ``/``); the worker
  joins it onto the app's ``base_url``. Absolute URLs are rejected.
- EXTERNAL ⇒ ``path_template`` is a well-formed absolute ``http(s)`` URL whose
  host is permitted by the app's ``allowed_hosts``. Relative paths and
  out-of-allowlist hosts are rejected. (Runtime stays https-only by default; the
  author-time check only requires a syntactically valid http(s) URL so the
  runtime scheme policy can tighten independently.)
"""
from __future__ import annotations

from urllib.parse import urlsplit

from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.gateway.app_kind import AppKind
from app.layer1_domain.value_objects.gateway.host_allowlist import HostAllowlist
from app.layer1_domain.value_objects.gateway.path_template import PathTemplate

_ALLOWED_URL_SCHEMES = ("http", "https")


def validate_function_target(
    *,
    app_kind: AppKind,
    allowed_hosts: HostAllowlist | None,
    path_template: PathTemplate,
) -> None:
    """Raise ``ValidationError`` if the path_template is wrong for the app kind."""
    if app_kind is AppKind.INTERNAL:
        if not path_template.is_relative():
            raise ValidationError(
                "internal function path_template must be a relative path starting "
                "with '/' (e.g. '/orders/{order_id}'), not an absolute URL"
            )
        return

    # EXTERNAL — path_template is the absolute target URL.
    parts = urlsplit(path_template.value)
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parts.netloc:
        raise ValidationError(
            "external function path_template must be an absolute http(s) URL "
            "(e.g. 'https://api.partner.com/v1/orders/{order_id}')"
        )
    host = parts.hostname or ""
    if allowed_hosts is None or allowed_hosts.is_empty():
        raise ValidationError(
            "external app has no allowed_hosts configured; cannot validate target host"
        )
    if not allowed_hosts.matches(host):
        raise ValidationError(
            f"external function host '{host}' is not in the app's allowed_hosts "
            f"{allowed_hosts.to_list()}"
        )
