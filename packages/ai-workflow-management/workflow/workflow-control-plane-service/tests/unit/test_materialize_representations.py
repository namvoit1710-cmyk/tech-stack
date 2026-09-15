"""SA-1905 D3 — materialize handles all THREE stored representations.

A file_ref may have been written in any of three shapes; materialize must
reconstruct each:

  (1) CSV tabular          — the normal store_data output (dict or list rows).
  (2) array_field artifact — top-level array-of-dicts split into a child CSV,
                             re-attached under its field name.
  (3) JSON-verbatim        — HTTP-node/raw-JSON payloads stored as-is (detected
                             by a json content-type or a leading brace/bracket),
                             parsed with json.loads so deep-path resolution over
                             the JSON body still works.

(1) and (2) round-trip through the real DataStoreService; (3) is exercised with
a minimal client that returns raw JSON bytes.
"""

from __future__ import annotations

from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


class _RawBytesClient:
    """Minimal file client that returns fixed bytes for any download."""

    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    async def download_file(self, file_id: str) -> bytes:
        return self._blob


def _store():
    return DataStoreService(MockFileServiceClient(), CsvConverter())


# ── Representation 1 — CSV tabular ──

async def test_rep1_csv_tabular_dict_roundtrip():
    store = _store()
    data = {"status": 200, "nested": {"deep": "v"}}
    ref = await store.store_data("n1", data)
    assert await store.materialize(ref) == data


async def test_rep1_csv_tabular_list_roundtrip():
    store = _store()
    data = [{"a": 1}, {"a": 2}]
    ref = await store.store_data("n2", data)
    assert await store.materialize(ref) == data


# ── Representation 2 — array_field artifact re-attach ──

async def test_rep2_array_field_artifact_reattach():
    store = _store()
    data = {"title": "order", "lines": [{"sku": "A"}, {"sku": "B"}]}
    ref = await store.store_data("n3", data)
    out = await store.materialize(ref)
    assert out["title"] == "order"
    assert out["lines"] == [{"sku": "A"}, {"sku": "B"}]


# ── Representation 3 — JSON-verbatim ──

async def test_rep3_json_verbatim_by_content_type():
    blob = b'{"a": 1, "nested": {"b": 2}, "list": [1, 2, 3]}'
    store = DataStoreService(_RawBytesClient(blob), CsvConverter())
    ref = build_file_ref(file_id="f", content_type="application/json")
    assert await store.materialize(ref) == {"a": 1, "nested": {"b": 2}, "list": [1, 2, 3]}


async def test_rep3_json_verbatim_array_by_sniff():
    blob = b'[{"x": 1}, {"x": 2}]'
    store = DataStoreService(_RawBytesClient(blob), CsvConverter())
    ref = build_file_ref(file_id="f", content_type="")  # no content_type → sniff '['
    assert await store.materialize(ref) == [{"x": 1}, {"x": 2}]


async def test_rep3_json_branch_falls_back_to_csv_on_parse_failure():
    # content_type SAYS json (so the json branch is entered) but the bytes are
    # actually CSV → json.loads fails, the branch swallows it and falls through
    # to the CSV parser instead of raising.
    blob = b"col_a,col_b\r\n1,2\r\n"
    store = DataStoreService(_RawBytesClient(blob), CsvConverter())
    ref = build_file_ref(file_id="f", content_type="application/json")
    out = await store.materialize(ref)
    assert out == {"col_a": 1, "col_b": 2}
