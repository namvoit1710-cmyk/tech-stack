"""Verify Keycloak-issued JWTs against the realm JWKS (SA-2533).

Used to authenticate a forwarded ``Authorization: Bearer`` token (service-to-
service, or any caller that presents a token rather than the BFF session
cookie). Only RS256 is accepted — ``alg: none`` and HMAC algs are rejected.

Implemented on ``cryptography`` (already a dependency) so no JWT library is
pulled in: fetch the realm JWKS, rebuild the RSA public key from the JWK, verify
the signature, then check issuer / audience / expiry.
"""
import base64
import json
import time

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.layer4_frameworks.config.app_config import settings

_JWKS_TTL = 3600.0


class JWTError(Exception):
    """Raised when a token fails verification."""


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class _JWKSCache:
    def __init__(self) -> None:
        self._keys: dict[str, dict] = {}
        self._fetched = 0.0

    def _refresh(self) -> None:
        url = settings.KEYCLOAK_ISSUER.rstrip("/") + "/protocol/openid-connect/certs"
        with httpx.Client(timeout=10.0) as client:
            data = client.get(url).json()
        self._keys = {k["kid"]: k for k in data.get("keys", []) if k.get("kid")}
        self._fetched = time.time()

    def get(self, kid: str) -> dict | None:
        if kid not in self._keys or (time.time() - self._fetched) > _JWKS_TTL:
            self._refresh()
        return self._keys.get(kid)


_jwks = _JWKSCache()


def _public_key(jwk: dict):
    n = int.from_bytes(_b64url(jwk["n"]), "big")
    e = int.from_bytes(_b64url(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def verify_access_token(token: str, audience: str | None = None, leeway: int = 30) -> dict:
    """Verify signature + iss/aud/exp and return the decoded claims (or raise ``JWTError``)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("malformed token")
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url(header_b64))
        payload = json.loads(_b64url(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise JWTError(f"undecodable token: {exc}") from exc

    if header.get("alg") != "RS256":
        raise JWTError(f"unsupported alg: {header.get('alg')}")

    jwk = _jwks.get(header.get("kid", ""))
    if not jwk:
        raise JWTError("unknown signing key")

    try:
        _public_key(jwk).verify(
            _b64url(sig_b64),
            f"{header_b64}.{payload_b64}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:  # cryptography raises InvalidSignature
        raise JWTError("bad signature") from exc

    now = time.time()
    if float(payload.get("exp", 0)) < now - leeway:
        raise JWTError("expired")
    if float(payload.get("nbf", 0)) > now + leeway:
        raise JWTError("not yet valid")
    if payload.get("iss", "").rstrip("/") != settings.KEYCLOAK_ISSUER.rstrip("/"):
        raise JWTError("bad issuer")

    expected_aud = settings.AUTH_JWT_AUDIENCE if audience is None else audience
    if expected_aud:
        token_aud = payload.get("aud")
        auds = token_aud if isinstance(token_aud, list) else [token_aud]
        if expected_aud not in auds:
            raise JWTError("bad audience")

    return payload
