"""SAP HANA ``IWorkerConfigRepository`` — SA-2437.

Per-workflow worker-instance pin, table ``AIW_WORKFLOW_WORKER_CONFIG``
(migration ``060_create_aiw_workflow_worker_config.sql``). Mirrors the
HanaTimerRepository template: ``:pN`` placeholders, upsert via
delete-then-insert (HANA has no portable ON CONFLICT), ``SELECTION`` stored as
JSON in an NCLOB column.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.layer1_domain.entities.workflow_worker_config import WorkflowWorkerConfig
from app.layer2_application.interfaces.worker_config_repository_interface import (
    IWorkerConfigRepository,
)
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin

logger = logging.getLogger(__name__)

_TABLE = "AIW_WORKFLOW_WORKER_CONFIG"
_COLS = "WORKFLOW_ID, TENANT_ID, MODE, SELECTION, UPDATED_AT, UPDATED_BY"

_DELETE_SQL = f"DELETE FROM {_TABLE} WHERE WORKFLOW_ID = :p0"
_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ({_COLS}) VALUES (:p0, :p1, :p2, :p3, :p4, :p5)"
)
_SELECT_BY_ID_SQL = f"SELECT {_COLS} FROM {_TABLE} WHERE WORKFLOW_ID = :p0"
_COUNT_BY_ID_SQL = f"SELECT COUNT(*) AS CNT FROM {_TABLE} WHERE WORKFLOW_ID = :p0"


class HanaWorkerConfigRepository(AsyncRepoMixin, IWorkerConfigRepository):
    def __init__(self, db: Any) -> None:
        self._db = db

    def get(self, workflow_id: str) -> WorkflowWorkerConfig | None:
        rows = self._db.execute_query(_SELECT_BY_ID_SQL, (workflow_id,))
        if not rows:
            return None
        return self._row_to_config(rows[0])

    def upsert(self, config: WorkflowWorkerConfig) -> None:
        self._db.execute_write(_DELETE_SQL, (config.workflow_id,))
        self._db.execute_write(
            _INSERT_SQL,
            (
                config.workflow_id,
                config.tenant_id,
                config.mode,
                json.dumps(config.selection or {}, default=str),
                config.updated_at,
                config.updated_by,
            ),
        )

    def delete(self, workflow_id: str) -> bool:
        rows = self._db.execute_query(_COUNT_BY_ID_SQL, (workflow_id,))
        existed = False
        if rows:
            cnt = rows[0].get("cnt", rows[0].get("CNT", 0))
            existed = bool(int(cnt)) if cnt is not None else False
        if existed:
            self._db.execute_write(_DELETE_SQL, (workflow_id,))
        return existed

    @staticmethod
    def _row_to_config(row: dict) -> WorkflowWorkerConfig:
        def _get(*keys: str) -> Any:
            for k in keys:
                if k in row and row[k] is not None:
                    return row[k]
            return None

        sel_raw = _get("selection", "SELECTION")
        if isinstance(sel_raw, dict):
            selection = sel_raw
        elif isinstance(sel_raw, str) and sel_raw:
            try:
                selection = json.loads(sel_raw)
            except json.JSONDecodeError:
                logger.warning("HanaWorkerConfigRepository: bad SELECTION json: %r", sel_raw)
                selection = {}
        else:
            selection = {}

        return WorkflowWorkerConfig(
            workflow_id=str(_get("workflow_id", "WORKFLOW_ID") or ""),
            tenant_id=str(_get("tenant_id", "TENANT_ID") or "system"),
            mode=str(_get("mode", "MODE") or "WHOLE_FLOW"),
            selection=selection,
            updated_at=str(_get("updated_at", "UPDATED_AT") or ""),
            updated_by=str(_get("updated_by", "UPDATED_BY") or ""),
        )
