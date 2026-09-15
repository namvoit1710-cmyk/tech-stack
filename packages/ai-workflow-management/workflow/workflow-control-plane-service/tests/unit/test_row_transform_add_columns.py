"""Unit: ``RowTransformFactoryService.add_columns`` — column creation via the
data-factory ``POST /api/v1/transformation`` ``mapping`` rule.

Verifies the service builds one ``mapping`` rule per column (``new_col`` +
``pl.lit(<default>)``) and unwraps the deployed v2 response shape
(``result.output_file_id`` / ``result.output_version_id``) into a
``RowTransformResult`` with the stable file_id and new version.
"""
from __future__ import annotations

from app.layer4_frameworks.services.row_transform_factory_service import (
    RowTransformFactoryService,
    _polars_literal,
)

# asyncio_mode = auto handles the async tests; no blanket mark (this module also
# has a sync test).


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def transform_data(self, file_id, rules, file_format="csv", output_format="csv"):
        self.calls.append({"file_id": file_id, "rules": rules})
        return self._response


def _v2_ok(file_id="file_1", version_id="ver_2", total_columns=4):
    return {
        "success": True,
        "result": {
            "success": True,
            "output_file_id": file_id,
            "output_version_id": version_id,
            "total_columns": total_columns,
            "total_rows": 2,
            "odata": {"result_file_url": f".../files/versions/{version_id}/download"},
        },
        "message": "Transformed successfully",
    }


async def test_add_columns_builds_mapping_rule_per_column():
    client = _FakeClient(_v2_ok())
    svc = RowTransformFactoryService(client)

    res = await svc.add_columns("file_1", ["c1", "c2"], version_id="ver_1")

    assert res.success
    assert res.file_id == "file_1"          # stable identity
    assert res.version_id == "ver_2"        # new revision
    rules = client.calls[0]["rules"]
    assert [r["params"]["new_col"] for r in rules] == ["c1", "c2"]
    assert all(r["type"] == "mapping" for r in rules)
    assert all(r["params"]["expression"] == "pl.lit('')" for r in rules)


async def test_add_columns_empty_list_is_noop():
    client = _FakeClient(_v2_ok())
    svc = RowTransformFactoryService(client)

    res = await svc.add_columns("file_1", [], version_id="ver_9")

    assert res.success
    assert res.file_id == "file_1"
    assert res.version_id == "ver_9"
    assert client.calls == []  # no round-trip when nothing to add


async def test_add_columns_missing_file_id_fails_fast():
    client = _FakeClient(_v2_ok())
    svc = RowTransformFactoryService(client)
    res = await svc.add_columns("", ["c1"])
    assert not res.success
    assert res.error_code == "INVALID_INPUT"
    assert client.calls == []


async def test_add_columns_factory_failure_surfaces_error():
    client = _FakeClient({"success": False, "message": "boom", "error_code": "FACTORY_ERROR"})
    svc = RowTransformFactoryService(client)
    res = await svc.add_columns("file_1", ["c1"])
    assert not res.success
    assert res.error_code == "FACTORY_ERROR"
    assert "boom" in res.message


async def test_add_columns_tolerates_legacy_top_level_file_id():
    # Older factory shape: identity echoed at the top level, not under result.
    client = _FakeClient({"success": True, "file_id": "file_9", "version_id": "ver_9"})
    svc = RowTransformFactoryService(client)
    res = await svc.add_columns("file_9", ["c1"])
    assert res.success
    assert res.file_id == "file_9"
    assert res.version_id == "ver_9"


def test_polars_literal_quoting():
    assert _polars_literal("") == "''"
    assert _polars_literal(None) == "''"
    assert _polars_literal("hi") == "'hi'"
    assert _polars_literal("it's") == "'it\\'s'"
    assert _polars_literal(5) == "5"
    assert _polars_literal(True) == "True"
