"""Regression: DataStoreService content-addressable upload dedup (#3).

``store_data`` passes a content-hash ``idempotency_key`` to the file service so
byte-identical re-uploads collapse to a single stored ``file_id`` instead of
allocating a fresh one every time (a node re-run, a re-executed test run, the
same input fanned out to a child workflow). Any real change to the data yields
different bytes -> a different key -> a new ``file_id``, so mutated data is
never aliased.
"""

from __future__ import annotations

import pytest

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import (
    DataStoreService,
    _content_key,
)
from app.layer4_frameworks.clients.mock_file_service_client import (
    MockFileServiceClient,
)


@pytest.fixture
def svc_fs():
    fs = MockFileServiceClient()
    return DataStoreService(fs, CsvConverter()), fs


def test_content_key_is_deterministic_and_content_sensitive():
    assert _content_key(b"a,b\n1,2\n") == _content_key(b"a,b\n1,2\n")
    assert _content_key(b"a,b\n1,2\n") != _content_key(b"a,b\n1,3\n")
    assert _content_key(b"x").startswith("sha256:")


@pytest.mark.asyncio
async def test_identical_dict_dedupes_to_same_file_id(svc_fs):
    svc, _ = svc_fs
    r1 = await svc.store_data("node_a", {"triggerId": "T-1", "objectType": "ORDER"})
    # Different logical key, identical content -> same underlying file_id.
    r2 = await svc.store_data("node_b", {"triggerId": "T-1", "objectType": "ORDER"})
    assert r1["file_id"]
    assert r1["file_id"] == r2["file_id"], "identical content should dedupe"


@pytest.mark.asyncio
async def test_changed_dict_gets_new_file_id(svc_fs):
    svc, _ = svc_fs
    r1 = await svc.store_data("node_a", {"triggerId": "T-1"})
    r2 = await svc.store_data("node_a", {"triggerId": "T-2"})  # edited value
    assert r1["file_id"] and r2["file_id"]
    assert r1["file_id"] != r2["file_id"], "changed content must not alias"


@pytest.mark.asyncio
async def test_repeated_store_uploads_once(svc_fs):
    """Re-storing identical content does not grow the file store (one POST)."""
    svc, fs = svc_fs
    await svc.store_data("n", {"a": 1, "b": 2})
    count_after_first = len(fs._store)
    await svc.store_data("n", {"a": 1, "b": 2})
    await svc.store_data("n", {"a": 1, "b": 2})
    assert len(fs._store) == count_after_first, (
        "identical re-uploads should dedupe, not allocate new files"
    )
