"""LaidonLLM SAP HANA persistence backend (SA-1488).

Makes SAP HANA a first-class database backend for the LiteLLM/LaidonLLM proxy,
selected at startup via ``DB_BACKEND=hana`` — replacing the Prisma/PostgreSQL
path with a HANA-backed client that is a drop-in substitute for ``PrismaClient``.
"""
from .config import (
    BACKEND_HANA,
    BACKEND_PRISMA,
    DB_BACKEND_ENV,
    HanaConfig,
    HanaConfigError,
    get_db_backend,
    is_hana_backend,
)
from .connection import HanaConnectionError, HanaConnectionManager

__all__ = [
    "BACKEND_HANA",
    "BACKEND_PRISMA",
    "DB_BACKEND_ENV",
    "HanaConfig",
    "HanaConfigError",
    "get_db_backend",
    "is_hana_backend",
    "HanaConnectionError",
    "HanaConnectionManager",
]
