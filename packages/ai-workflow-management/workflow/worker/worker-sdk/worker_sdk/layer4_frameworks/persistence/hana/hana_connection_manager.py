"""
SAP HANA connection manager stub.

Manages a pool of connections to SAP HANA. In production this would
use hdbcli or a similar driver. Currently a no-op placeholder.
"""
from typing import Any


class HanaConnectionManager:
    """Stub HANA connection manager."""

    def __init__(self, address: str = "", port: int = 0, user: str = "", password: str = "") -> None:
        self.address = address
        self.port = port
        self.user = user
        self.password = password

    def get_connection(self) -> Any:
        raise NotImplementedError("HanaConnectionManager is a stub.")

    def close(self) -> None:
        pass
