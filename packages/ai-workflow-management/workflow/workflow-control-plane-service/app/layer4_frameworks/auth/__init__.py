"""Keycloak SSO / Backend-For-Frontend auth (SA-2530).

The control plane can act as a BFF: it runs the OIDC Authorization Code + PKCE
flow against Keycloak (Azure-federated) and hands the browser an opaque
HttpOnly session cookie. The real OIDC tokens stay server-side in the
``SessionStore`` — an XSS on the SPA cannot lift them.

Gated behind ``settings.AUTH_ENABLED`` (default off): when unconfigured, the
service behaves exactly as before (identity via the legacy ``X-User`` header).
"""
