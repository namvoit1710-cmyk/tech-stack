"""Unit tests for the JWKS-based JWT verifier (SA-2533)."""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.layer4_frameworks.auth import jwt_verify
from app.layer4_frameworks.config.app_config import settings

ISSUER = "https://kc.example/realms/tenant-1"
KID = "test-kid"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def signing(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()
    jwk = {
        "kid": KID,
        "kty": "RSA",
        "alg": "RS256",
        "n": _b64url(pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big")),
    }
    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", ISSUER)
    monkeypatch.setattr(settings, "AUTH_JWT_AUDIENCE", "smdg-ai-api")
    # Serve our JWK from the cache; never hit the network.
    monkeypatch.setattr(jwt_verify._jwks, "_keys", {KID: jwk})
    monkeypatch.setattr(jwt_verify._jwks, "_fetched", time.time())
    monkeypatch.setattr(jwt_verify._jwks, "_refresh", lambda: None)

    def make(payload, alg="RS256", kid=KID):
        header = {"alg": alg, "typ": "JWT", "kid": kid}
        h = _b64url(json.dumps(header).encode())
        p = _b64url(json.dumps(payload).encode())
        sig = key.sign(f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{h}.{p}.{_b64url(sig)}"

    return make


def _claims(**over):
    base = {"sub": "u-1", "email": "felix@laidon.com", "iss": ISSUER,
            "aud": "smdg-ai-api", "exp": time.time() + 300,
            "realm_access": {"roles": ["user"]}}
    base.update(over)
    return base


def test_valid_token(signing):
    claims = jwt_verify.verify_access_token(signing(_claims()))
    assert claims["sub"] == "u-1"


def test_expired_rejected(signing):
    with pytest.raises(jwt_verify.JWTError):
        jwt_verify.verify_access_token(signing(_claims(exp=time.time() - 100)))


def test_wrong_audience_rejected(signing):
    with pytest.raises(jwt_verify.JWTError):
        jwt_verify.verify_access_token(signing(_claims(aud="someone-else")))


def test_wrong_issuer_rejected(signing):
    with pytest.raises(jwt_verify.JWTError):
        jwt_verify.verify_access_token(signing(_claims(iss="https://evil/realms/x")))


def test_tampered_signature_rejected(signing):
    tok = signing(_claims())
    h, p, _ = tok.split(".")
    forged = _claims(sub="attacker")
    p2 = _b64url(json.dumps(forged).encode())
    with pytest.raises(jwt_verify.JWTError):
        jwt_verify.verify_access_token(f"{h}.{p2}.{tok.split('.')[2]}")


def test_alg_none_rejected(signing):
    with pytest.raises(jwt_verify.JWTError):
        jwt_verify.verify_access_token(signing(_claims(), alg="none"))
