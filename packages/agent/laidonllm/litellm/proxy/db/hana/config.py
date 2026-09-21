"""HANA backend configuration for LaidonLLM (SA-1488).

Leaf module: stdlib-only imports so it can be loaded and unit-tested
without importing the full ``litellm`` package. Selects the persistence
backend via ``DB_BACKEND`` and validates SAP HANA connection settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

DB_BACKEND_ENV = "DB_BACKEND"
BACKEND_PRISMA = "prisma"
BACKEND_HANA = "hana"
_SUPPORTED_BACKENDS = (BACKEND_PRISMA, BACKEND_HANA)


class HanaConfigError(ValueError):
    """Raised when DB_BACKEND=hana is selected but its configuration is invalid/missing."""


def get_db_backend() -> str:
    """Return the configured DB backend (``prisma`` default). Raises on unsupported values."""
    raw = (os.getenv(DB_BACKEND_ENV) or BACKEND_PRISMA).strip().lower()
    if raw not in _SUPPORTED_BACKENDS:
        raise ValueError(
            f"{DB_BACKEND_ENV}={raw!r} is not supported. Use one of: {', '.join(repr(b) for b in _SUPPORTED_BACKENDS)}."
        )
    return raw


def is_hana_backend() -> bool:
    """True when the proxy should persist through SAP HANA instead of Prisma."""
    return get_db_backend() == BACKEND_HANA


def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class HanaConfig:
    """Resolved SAP HANA connection settings."""

    host: str
    port: int
    user: str
    password: str
    encrypt: bool = True
    validate_cert: bool = False
    schema: Optional[str] = None
    connect_timeout_ms: int = 15000

    @classmethod
    def from_env(cls) -> "HanaConfig":
        """Build config from HANA_* env vars. Fail-fast (AC2) with a clear message
        when required settings are missing."""
        missing = [k for k in ("HANA_HOST", "HANA_USER", "HANA_PASSWORD") if not os.getenv(k)]
        if missing:
            raise HanaConfigError(
                "DB_BACKEND=hana requires the following environment variable(s): "
                + ", ".join(missing)
                + ". Provide them (e.g. HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD, "
                "HANA_ENCRYPT, HANA_SCHEMA)."
            )
        try:
            port = int(os.getenv("HANA_PORT", "443"))
        except ValueError as e:
            raise HanaConfigError(f"HANA_PORT must be an integer: {e}") from e
        try:
            connect_timeout_ms = int(os.getenv("HANA_CONNECT_TIMEOUT_MS", "15000"))
        except ValueError as e:
            raise HanaConfigError(f"HANA_CONNECT_TIMEOUT_MS must be an integer: {e}") from e
        return cls(
            host=os.environ["HANA_HOST"],
            port=port,
            user=os.environ["HANA_USER"],
            password=os.environ["HANA_PASSWORD"],
            encrypt=_bool_env("HANA_ENCRYPT", True),
            validate_cert=_bool_env("HANA_VALIDATE_CERT", False),
            schema=os.getenv("HANA_SCHEMA") or None,
            connect_timeout_ms=connect_timeout_ms,
        )

    def safe_repr(self) -> str:
        """Connection summary without secrets (for logs)."""
        return (
            f"HanaConfig(host={self.host!r}, port={self.port}, user={self.user!r}, "
            f"encrypt={self.encrypt}, schema={self.schema!r})"
        )

    def __repr__(self) -> str:
        # R2: the dataclass-generated repr prints `password` verbatim, and a
        # dataclass's __str__ falls back to __repr__ — delegating here closes
        # the leak in both repr(cfg) and str(cfg) with one override.
        return self.safe_repr()
