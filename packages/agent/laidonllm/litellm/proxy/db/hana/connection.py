"""SAP HANA connection management for LaidonLLM (SA-1488).

Wraps ``hdbcli.dbapi``. The driver is imported lazily so this module can be
imported (and its validation/fail-fast paths unit-tested) without the driver
installed. Per AC2, connection failures raise ``HanaConnectionError`` fast and
loudly rather than degrading silently.

Leaf module: takes a duck-typed config object (see ``config.HanaConfig``) so it
carries no import from the wider package and stays standalone-testable.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger("laidonllm.hana")


class HanaConnectionError(RuntimeError):
    """Raised when a SAP HANA connection cannot be established or used (fail-fast, AC2)."""


def _import_dbapi():
    try:
        from hdbcli import dbapi  # type: ignore
        return dbapi
    except Exception as e:  # ImportError or a partial install
        raise HanaConnectionError(
            "The 'hdbcli' SAP HANA driver is not installed or failed to import. "
            "Install it (pip install hdbcli) to use DB_BACKEND=hana."
        ) from e


class HanaConnectionManager:
    """Owns a single SAP HANA connection with lazy connect + health check.

    Deliberately minimal for iteration 1 (single connection, autocommit). A
    pooled implementation lands with the repository layer.
    """

    def __init__(self, config: Any):
        self._cfg = config
        self._conn: Optional[Any] = None
        self._lock = threading.RLock()

    def connect(self) -> Any:
        """Open a HANA connection, applying the configured schema. Fail-fast on error."""
        dbapi = _import_dbapi()
        cfg = self._cfg
        try:
            conn = dbapi.connect(
                address=cfg.host,
                port=int(cfg.port),
                user=cfg.user,
                password=cfg.password,
                encrypt=bool(getattr(cfg, "encrypt", True)),
                sslValidateCertificate=bool(getattr(cfg, "validate_cert", False)),
                connectTimeout=int(getattr(cfg, "connect_timeout_ms", 15000)),
                autocommit=True,
            )
        except Exception as e:
            raise HanaConnectionError(
                f"Failed to connect to SAP HANA at {cfg.host}:{cfg.port} as {cfg.user}: {e}"
            ) from e

        schema = getattr(cfg, "schema", None)
        if schema:
            try:
                cur = conn.cursor()
                cur.execute(f'SET SCHEMA "{schema}"')
                cur.close()
            except Exception as e:
                try:
                    conn.close()
                except Exception:
                    pass
                raise HanaConnectionError(f"Failed to SET SCHEMA {schema!r} on HANA: {e}") from e

        with self._lock:
            self._conn = conn
        logger.info(
            "Connected to SAP HANA at %s:%s (schema=%s)", cfg.host, cfg.port, schema or "<default>"
        )
        return conn

    def ensure_connected(self) -> Any:
        with self._lock:
            if self._conn is not None:
                return self._conn
        return self.connect()

    def health_check(self) -> bool:
        """Run HANA's canonical ``SELECT 1 FROM DUMMY`` liveness probe."""
        conn = self.ensure_connected()
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM DUMMY")
            row = cur.fetchone()
            return bool(row and row[0] == 1)
        except Exception as e:
            raise HanaConnectionError(f"SAP HANA health check failed: {e}") from e
        finally:
            cur.close()

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        conn = self.ensure_connected()
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
