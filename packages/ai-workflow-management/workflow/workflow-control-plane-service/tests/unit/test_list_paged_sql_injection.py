"""list_paged must BIND $id/$status/$version, never interpolate them.

These params come from user-supplied OData query args ($id/$status/$version on
the version-list endpoint). The old build f-stringed them into the SQL (SQL
injection) and double-quoted STATUS/VERSION (HANA identifier quotes, not string
literals). This pins the bound form — it fails if anyone reverts to interpolation.
"""
from __future__ import annotations

from app.layer2_application.repositories.workflow_repository_interface import ListWorkflowsFilter
from app.layer4_frameworks.persistence.hana.hana_workflow_repo import HanaWorkflowRepository


class _CapturingDb:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple]] = []

    def execute_query(self, sql: str, params: tuple = ()):
        self.queries.append((sql, params))
        return [{"cnt": 0}] if "COUNT(*)" in sql else []

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        pass


def test_list_paged_binds_id_status_version_not_interpolated():
    evil = "x' OR '1'='1"
    db = _CapturingDb()
    repo = HanaWorkflowRepository(db=db)
    repo.list_paged(ListWorkflowsFilter(id=evil, status="DRAFT", version="v-1", top=10, skip=0))

    for sql, params in db.queries:
        assert evil not in sql, f"injected value leaked into SQL: {sql}"
        assert 'STATUS = "' not in sql, "double-quoted STATUS is a HANA identifier, not a literal"
        assert 'VERSION = "' not in sql, "double-quoted VERSION is a HANA identifier, not a literal"
        # the values must travel as bind params instead
        assert evil in params and "DRAFT" in params and "v-1" in params
