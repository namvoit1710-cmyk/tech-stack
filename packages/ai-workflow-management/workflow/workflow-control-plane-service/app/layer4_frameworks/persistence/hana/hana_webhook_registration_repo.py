"""SAP HANA implementation of ``IWebhookRegistrationRepository`` — SA-2059 F2.

Durable inbound-webhook store for SAP BTP production deployments. Mirrors the
semantics of ``MemoryWebhookRegistrationRepository`` exactly — same save /
get_by_id / get_by_path / list_paged / delete / record_delivery /
list_deliveries contract — but persists to the ``AIW_WEBHOOK_REGISTRATIONS`` and
``AIW_WEBHOOK_DELIVERIES`` tables (migration
``051_create_aiw_webhook_registrations.sql``).

Follows the HANA repository template used by ``HanaTimerRepository``:

- Constructor takes an ``IDatabaseConnection`` (production:
  ``HanaConnectionManager``; tests: stub) exposing ``execute_query(sql, params)``
  / ``execute_write(sql, params)`` with ``:p0``, ``:p1``, … named placeholders.
- Registration ``save`` is an upsert via delete-then-insert on ``ID`` (HANA has
  no portable ``ON CONFLICT``).
- ``ip_allowlist`` is a list → stored as a JSON array in an ``NCLOB`` column.
- Result rows are read with both lower-case (``HanaConnectionManager``) and
  upper-case (stub) column names, like ``_row_to_timer``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.layer1_domain.entities.webhook_registration import (
    WebhookDelivery,
    WebhookRegistration,
)
from app.layer2_application.repositories.webhook_registration_repository_interface import (
    IWebhookRegistrationRepository,
    ListRegistrationsFilter,
    ListRegistrationsPage,
)
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin

logger = logging.getLogger(__name__)

_REG_TABLE = "AIW_WEBHOOK_REGISTRATIONS"
_DELIVERY_TABLE = "AIW_WEBHOOK_DELIVERIES"

# Match MemoryWebhookRegistrationRepository's per-registration ring size.
_MAX_DELIVERIES_PER_REG = 100

_REG_COLS = (
    "ID, NAME, PATH, SOURCE, SECRET, HMAC_SECRET, EMIT_TOPIC, IP_ALLOWLIST, "
    "INTEGRATION_ACTOR, TENANT_ID, ENABLED, CREATED_AT, UPDATED_AT"
)

_REG_DELETE_SQL = f"DELETE FROM {_REG_TABLE} WHERE ID = :p0"

_REG_INSERT_SQL = (
    f"INSERT INTO {_REG_TABLE} ({_REG_COLS}) "
    f"VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :p12)"
)

_REG_SELECT_BY_ID_SQL = f"SELECT {_REG_COLS} FROM {_REG_TABLE} WHERE ID = :p0"

_REG_SELECT_BY_PATH_SQL = f"SELECT {_REG_COLS} FROM {_REG_TABLE} WHERE PATH = :p0"

_DELIVERY_COLS = (
    "ID, REGISTRATION_ID, RECEIVED_AT, OUTCOME, STATUS_CODE, "
    "EVENT_TOPIC, EVENT_ID, DETAIL, PAYLOAD"
)

_DELIVERY_INSERT_SQL = (
    f"INSERT INTO {_DELIVERY_TABLE} ({_DELIVERY_COLS}) "
    f"VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8)"
)

_DELIVERY_SELECT_SQL = (
    f"SELECT {_DELIVERY_COLS} FROM {_DELIVERY_TABLE} "
    f"WHERE REGISTRATION_ID = :p0 ORDER BY RECEIVED_AT DESC LIMIT :p1"
)

_DELIVERY_SELECT_ONE_SQL = (
    f"SELECT {_DELIVERY_COLS} FROM {_DELIVERY_TABLE} "
    f"WHERE REGISTRATION_ID = :p0 AND ID = :p1"
)

# Trim: keep the newest _MAX rows per registration. Delete anything older than
# the RECEIVED_AT of the row at that cutoff. Correlated to the same registration.
_DELIVERY_TRIM_SQL = (
    f"DELETE FROM {_DELIVERY_TABLE} WHERE REGISTRATION_ID = :p0 "
    f"AND RECEIVED_AT < ("
    f"SELECT MIN(RECEIVED_AT) FROM ("
    f"SELECT RECEIVED_AT FROM {_DELIVERY_TABLE} WHERE REGISTRATION_ID = :p1 "
    f"ORDER BY RECEIVED_AT DESC LIMIT :p2))"
)

_DELIVERY_DELETE_BY_REG_SQL = (
    f"DELETE FROM {_DELIVERY_TABLE} WHERE REGISTRATION_ID = :p0"
)


class HanaWebhookRegistrationRepository(AsyncRepoMixin, IWebhookRegistrationRepository):
    """SAP HANA-backed ``IWebhookRegistrationRepository``."""

    def __init__(self, db: Any) -> None:
        """Construct with an ``IDatabaseConnection`` instance.

        ``db`` must expose ``execute_query(sql, params)`` and
        ``execute_write(sql, params)``. In production this is
        ``HanaConnectionManager``; tests pass a stub.
        """
        self._db = db

    # -- registrations -----------------------------------------------------

    def save(self, registration: WebhookRegistration) -> None:
        """Upsert ``id`` — delete any prior row then insert."""
        self._db.execute_write(_REG_DELETE_SQL, (registration.id,))
        self._db.execute_write(
            _REG_INSERT_SQL,
            (
                registration.id,
                registration.name,
                registration.path,
                registration.source,
                registration.secret,
                registration.hmac_secret,
                registration.emit_topic,
                json.dumps(list(registration.ip_allowlist or []), default=str),
                registration.integration_actor,
                registration.tenant_id,
                "1" if registration.enabled else "0",
                registration.created_at,
                registration.updated_at,
            ),
        )

    def get_by_id(self, id: str) -> WebhookRegistration | None:
        rows = self._db.execute_query(_REG_SELECT_BY_ID_SQL, (id,))
        if not rows:
            return None
        return self._row_to_registration(rows[0])

    def get_by_path(self, path: str, tenant_id: str | None = None) -> WebhookRegistration | None:
        """Resolve a registration by ingress path (leading ``/`` normalized).

        Mirrors the memory repo: the stored ``path`` may or may not carry a
        leading slash, so both variants are queried and compared normalized.
        """
        norm = path if path.startswith("/") else f"/{path}"
        # Query both stored spellings (with/without leading slash); the memory
        # repo normalizes at compare time, so we filter in Python to match.
        rows = self._db.execute_query(_REG_SELECT_BY_PATH_SQL, (path,))
        if not rows:
            alt = path[1:] if path.startswith("/") else f"/{path}"
            rows = self._db.execute_query(_REG_SELECT_BY_PATH_SQL, (alt,))
        for row in rows:
            reg = self._row_to_registration(row)
            reg_path = reg.path if reg.path.startswith("/") else f"/{reg.path}"
            if reg_path != norm:
                continue
            if tenant_id is not None and reg.tenant_id != tenant_id:
                continue
            return reg
        return None

    def list_paged(self, filters: ListRegistrationsFilter) -> ListRegistrationsPage:
        """Filter by tenant/source, paginate with skip/top, return total.

        Builds the WHERE clause dynamically so an absent filter is not a bound
        empty-string comparison. Total is a separate COUNT over the same WHERE.
        """
        where = []
        params: list[Any] = []
        if filters.tenant_id:
            where.append(f"TENANT_ID = :p{len(params)}")
            params.append(filters.tenant_id)
        if filters.source:
            where.append(f"SOURCE = :p{len(params)}")
            params.append(filters.source)
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""

        count_sql = f"SELECT COUNT(*) AS CNT FROM {_REG_TABLE}{where_sql}"
        crows = self._db.execute_query(count_sql, tuple(params))
        total = 0
        if crows:
            cnt = crows[0].get("cnt", crows[0].get("CNT", 0))
            total = int(cnt) if cnt is not None else 0

        page_sql = (
            f"SELECT {_REG_COLS} FROM {_REG_TABLE}{where_sql} "
            f"ORDER BY CREATED_AT LIMIT :p{len(params)} OFFSET :p{len(params) + 1}"
        )
        page_params = tuple(params) + (int(filters.top), int(filters.skip))
        rows = self._db.execute_query(page_sql, page_params)
        items = [self._row_to_registration(row) for row in rows]
        return ListRegistrationsPage(items=items, total=total)

    def delete(self, id: str) -> None:
        """Delete the registration and its deliveries (memory repo drops both)."""
        self._db.execute_write(_REG_DELETE_SQL, (id,))
        self._db.execute_write(_DELIVERY_DELETE_BY_REG_SQL, (id,))

    # -- deliveries --------------------------------------------------------

    def record_delivery(self, delivery: WebhookDelivery) -> None:
        """Insert a delivery, then trim the registration's log to the cap."""
        self._db.execute_write(
            _DELIVERY_INSERT_SQL,
            (
                delivery.id,
                delivery.registration_id,
                delivery.received_at,
                delivery.outcome,
                int(delivery.status_code),
                delivery.event_topic,
                delivery.event_id,
                delivery.detail,
                delivery.payload,
            ),
        )
        # ponytail: trim on every insert keeps the log bounded like the memory
        # deque(maxlen=100). Fine at webhook volumes; if record_delivery becomes
        # hot, batch the trim (every Nth insert) instead.
        self._db.execute_write(
            _DELIVERY_TRIM_SQL,
            (
                delivery.registration_id,
                delivery.registration_id,
                _MAX_DELIVERIES_PER_REG,
            ),
        )

    def list_deliveries(self, registration_id: str, limit: int = 50) -> list[WebhookDelivery]:
        rows = self._db.execute_query(
            _DELIVERY_SELECT_SQL, (registration_id, int(limit))
        )
        return [self._row_to_delivery(row) for row in rows]

    def get_delivery(self, registration_id: str, delivery_id: str) -> WebhookDelivery | None:
        rows = self._db.execute_query(
            _DELIVERY_SELECT_ONE_SQL, (registration_id, delivery_id)
        )
        return self._row_to_delivery(rows[0]) if rows else None

    # -- row mappers -------------------------------------------------------

    @staticmethod
    def _get(row: dict, *keys: str) -> Any:
        for k in keys:
            if k in row and row[k] is not None:
                return row[k]
        return None

    @classmethod
    def _row_to_registration(cls, row: dict) -> WebhookRegistration:
        """Build a ``WebhookRegistration`` from a HANA result row.

        Handles both lower-case (``HanaConnectionManager``) and upper-case
        (stub) column names.
        """
        allowlist_raw = cls._get(row, "ip_allowlist", "IP_ALLOWLIST")
        if isinstance(allowlist_raw, list):
            ip_allowlist = [str(x) for x in allowlist_raw]
        elif isinstance(allowlist_raw, str) and allowlist_raw:
            try:
                parsed = json.loads(allowlist_raw)
                ip_allowlist = [str(x) for x in parsed] if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                logger.warning(
                    "HanaWebhookRegistrationRepository: failed to decode "
                    "ip_allowlist JSON: %r",
                    allowlist_raw,
                )
                ip_allowlist = []
        else:
            ip_allowlist = []

        enabled_raw = cls._get(row, "enabled", "ENABLED")
        enabled = str(enabled_raw) not in ("0", "false", "False", "None")

        updated_at = cls._get(row, "updated_at", "UPDATED_AT")

        return WebhookRegistration(
            id=str(cls._get(row, "id", "ID") or ""),
            name=str(cls._get(row, "name", "NAME") or ""),
            path=str(cls._get(row, "path", "PATH") or ""),
            source=str(cls._get(row, "source", "SOURCE") or "generic"),
            secret=str(cls._get(row, "secret", "SECRET") or ""),
            hmac_secret=str(cls._get(row, "hmac_secret", "HMAC_SECRET") or ""),
            emit_topic=str(cls._get(row, "emit_topic", "EMIT_TOPIC") or ""),
            ip_allowlist=ip_allowlist,
            integration_actor=str(cls._get(row, "integration_actor", "INTEGRATION_ACTOR") or ""),
            tenant_id=str(cls._get(row, "tenant_id", "TENANT_ID") or "system"),
            enabled=enabled,
            created_at=str(cls._get(row, "created_at", "CREATED_AT") or ""),
            updated_at=str(updated_at) if updated_at is not None else None,
        )

    @classmethod
    def _row_to_delivery(cls, row: dict) -> WebhookDelivery:
        status_raw = cls._get(row, "status_code", "STATUS_CODE")
        try:
            status_code = int(status_raw) if status_raw is not None else 0
        except (TypeError, ValueError):
            status_code = 0
        return WebhookDelivery(
            id=str(cls._get(row, "id", "ID") or ""),
            registration_id=str(cls._get(row, "registration_id", "REGISTRATION_ID") or ""),
            received_at=str(cls._get(row, "received_at", "RECEIVED_AT") or ""),
            outcome=str(cls._get(row, "outcome", "OUTCOME") or ""),
            status_code=status_code,
            event_topic=str(cls._get(row, "event_topic", "EVENT_TOPIC") or ""),
            event_id=str(cls._get(row, "event_id", "EVENT_ID") or ""),
            detail=str(cls._get(row, "detail", "DETAIL") or ""),
            payload=str(cls._get(row, "payload", "PAYLOAD") or ""),
        )


if __name__ == "__main__":
    # ponytail: in-memory fake IDatabaseConnection self-check — exercises the
    # upsert, path lookup, tenant filter, paging, delivery trim, and row mapping
    # without a live HANA. Runs with `python hana_webhook_registration_repo.py`.
    import re

    class _FakeDB:
        """Minimal SQL fake: enough of INSERT/DELETE/SELECT for this repo."""

        def __init__(self) -> None:
            self.regs: list[dict] = []
            self.dels: list[dict] = []

        def _bind(self, sql: str, params):
            for i in range(len(params) - 1, -1, -1):
                sql = sql.replace(f":p{i}", repr(params[i]))
            return sql

        def execute_write(self, sql, params=()):
            table = self.regs if _REG_TABLE in sql else self.dels
            if sql.strip().startswith("DELETE"):
                if _DELIVERY_TRIM_SQL.split("WHERE")[0] in sql and "MIN" in sql:
                    reg = params[0]
                    keep = params[2]
                    rows = sorted(
                        [d for d in self.dels if d["REGISTRATION_ID"] == reg],
                        key=lambda d: d["RECEIVED_AT"], reverse=True,
                    )[:keep]
                    keep_ids = {d["ID"] for d in rows}
                    self.dels = [
                        d for d in self.dels
                        if d["REGISTRATION_ID"] != reg or d["ID"] in keep_ids
                    ]
                elif _REG_TABLE in sql:
                    self.regs = [r for r in self.regs if r["ID"] != params[0]]
                else:
                    self.dels = [d for d in self.dels if d["REGISTRATION_ID"] != params[0]]
                return
            # INSERT
            cols = re.search(r"\(([^)]*)\) VALUES", sql).group(1)
            names = [c.strip() for c in cols.split(",")]
            table.append(dict(zip(names, params)))

        def execute_query(self, sql, params=()):
            if "COUNT(*)" in sql:
                rows = self._filter_regs(sql, params)
                return [{"CNT": len(rows)}]
            if _DELIVERY_TABLE in sql:
                reg = params[0]
                limit = params[1]
                rows = sorted(
                    [d for d in self.dels if d["REGISTRATION_ID"] == reg],
                    key=lambda d: d["RECEIVED_AT"], reverse=True,
                )
                return rows[:limit]
            # registration select (by id / path / paged)
            if "WHERE ID =" in sql:
                return [r for r in self.regs if r["ID"] == params[0]]
            if "WHERE PATH =" in sql:
                return [r for r in self.regs if r.get("PATH") == params[0]]
            rows = self._filter_regs(sql, params)
            rows = sorted(rows, key=lambda r: r.get("CREATED_AT") or "")
            if "LIMIT" in sql:
                top, skip = params[-2], params[-1]
                rows = rows[skip:skip + top]
            return rows

        def _filter_regs(self, sql, params):
            rows = list(self.regs)
            pi = 0
            if "TENANT_ID =" in sql:
                rows = [r for r in rows if r.get("TENANT_ID") == params[pi]]; pi += 1
            if "SOURCE =" in sql:
                rows = [r for r in rows if r.get("SOURCE") == params[pi]]; pi += 1
            return rows

    repo = HanaWebhookRegistrationRepository(_FakeDB())

    r = WebhookRegistration(id="r1", name="Jira", path="jira", source="jira",
                            emit_topic="t.jira", ip_allowlist=["10.0.0.0/8"],
                            tenant_id="acme")
    repo.save(r)
    got = repo.get_by_id("r1")
    assert got is not None and got.ip_allowlist == ["10.0.0.0/8"], got
    assert got.enabled is True

    # get_by_path normalizes leading slash both ways
    assert repo.get_by_path("/jira") is not None
    assert repo.get_by_path("jira", tenant_id="acme") is not None
    assert repo.get_by_path("jira", tenant_id="other") is None

    # upsert: save again with a change, still one row
    r.name = "Jira2"
    repo.save(r)
    assert repo.get_by_id("r1").name == "Jira2"

    # list_paged tenant/source filter + total
    repo.save(WebhookRegistration(id="r2", path="gh", source="github", tenant_id="acme"))
    page = repo.list_paged(ListRegistrationsFilter(tenant_id="acme", top=1, skip=0))
    assert page.total == 2, page.total
    assert len(page.items) == 1

    # deliveries: newest-first, trimmed to cap
    for i in range(_MAX_DELIVERIES_PER_REG + 20):
        repo.record_delivery(WebhookDelivery(
            id=f"d{i}", registration_id="r1",
            received_at=f"2026-01-01T00:{i:04d}", outcome="published", status_code=200,
        ))
    ds = repo.list_deliveries("r1", limit=5)
    assert len(ds) == 5, len(ds)
    assert ds[0].received_at > ds[1].received_at  # newest first
    all_ds = repo.list_deliveries("r1", limit=1000)
    assert len(all_ds) == _MAX_DELIVERIES_PER_REG, len(all_ds)  # trimmed

    # delete drops registration + its deliveries
    repo.delete("r1")
    assert repo.get_by_id("r1") is None
    assert repo.list_deliveries("r1") == []

    print("hana_webhook_registration_repo self-check OK")
