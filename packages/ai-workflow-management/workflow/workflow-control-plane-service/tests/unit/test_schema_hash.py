"""``schema_hash`` — the key the node-output schema dictionary dedupes on.

Design + measurements: ``solace/specs/wcp-node-output-storage-redesign.md`` §3.1.

Measured on tenant-1: 2,702 refs collapse to **481 distinct** `(columns,
column_types)` pairs — 5.6x, 31.51 MB down to 7.908 MB. That entire win rests on
two canonicalisation rules, and each one fails **silently** in a different
direction:

* sort the mapping, or the same schema hashes differently by insertion order,
  dedup decays toward 1x, and nothing reports it;
* do NOT sort the list, or two genuinely different CSVs share a hash and the
  frontend renders one node's columns under another node's name.

Both are the kind of defect that ships. So the golden vectors below are pinned:
a canonicalisation change has to be a deliberate edit to this file, not a
silent drift in dedup rate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from app.layer1_domain.entities.data_reference import (
    add_artifact,
    build_file_ref,
    schema_hash,
)


class TestCanonicalisation:
    def test_mapping_order_is_not_part_of_the_schema(self):
        """The 5.6x. `column_types` is a mapping — order carries no meaning."""
        a = schema_hash(["id", "name"], {"id": "number", "name": "string"})
        b = schema_hash(["id", "name"], {"name": "string", "id": "number"})
        assert a == b, (
            "same schema, different dict insertion order, different hash — "
            "dedup would decay toward 1x with no error anywhere"
        )

    def test_column_order_IS_part_of_the_schema(self):
        """`columns` is the CSV header order. Sorting it would merge two
        different files onto one schema row, and the FE would render the wrong
        columns for a node — worse than no dedup at all."""
        a = schema_hash(["id", "name"], {"id": "number", "name": "string"})
        b = schema_hash(["name", "id"], {"id": "number", "name": "string"})
        assert a != b, "column order must change the hash"

    def test_a_changed_type_changes_the_hash(self):
        """False dedup is the dangerous direction: it shows one node's schema
        under another node's output."""
        a = schema_hash(["id"], {"id": "number"})
        b = schema_hash(["id"], {"id": "string"})
        assert a != b

    def test_a_changed_column_name_changes_the_hash(self):
        assert schema_hash(["id"], {}) != schema_hash(["ID"], {})

    def test_no_schema_hashes_to_empty_not_to_a_shared_row(self):
        """A value-mode CSV or an empty payload has nothing to point at.
        Returning a real hash here would herd every schema-less ref onto one
        meaningless dictionary row."""
        assert schema_hash(None, None) == ""
        assert schema_hash([], {}) == ""

    def test_columns_alone_and_types_alone_both_hash(self):
        assert schema_hash(["a"], None) != ""
        assert schema_hash(None, {"a": "string"}) != ""
        assert schema_hash(["a"], None) != schema_hash(None, {"a": "string"})


class TestItFitsTheColumn:
    def test_hash_is_exactly_64_chars(self):
        """`AIW_SCHEMA_DEF.SCHEMA_HASH` is NVARCHAR(64) and
        `AIW_RUN_NODE_OUTPUT.SCHEMA_HASH` matches it. A wider digest would be
        truncated on write — which silently creates collisions."""
        h = schema_hash([f"col_{i}" for i in range(537)],  # the measured max
                        {f"col_{i}": "string" for i in range(537)})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_a_537_column_schema_still_hashes(self):
        """p99 is 503 columns and the measured max is 537. The hash must not
        care about descriptor size — that is the entire point of storing it
        once instead of 2,702 times."""
        big = [f"a.b.c.column_with_a_long_name_{i}" for i in range(537)]
        assert schema_hash(big, {c: "string" for c in big}) != ""


class TestStableAcrossProcesses:
    """`hash()` is salted per process. If it ever creeps in here, every restart
    re-hashes every schema: the dictionary grows without bound while every
    individual read still looks correct."""

    GOLDEN = {
        # (columns, column_types) -> sha256 of the canonical form
        "simple": (["id", "name"], {"id": "number", "name": "string"}),
        "cols_only": (["x"], {}),
        "types_only": ([], {"x": "json"}),
        "unicode": (["tên", "giá"], {"tên": "string", "giá": "number"}),
    }

    def test_hash_is_reproducible_in_a_FRESH_interpreter(self):
        """Not a re-call in this process — a genuinely new one, which is the
        only way to catch a salted hash.

        The child loads ``data_reference`` straight off its path instead of
        importing ``app...``: the module needs only the stdlib, and pulling in
        the whole package costs seconds of CPU that this suite pays for
        elsewhere. ``test_event_mesh_dlq_durability`` polls a spool for exactly
        1.0s against a real DNS lookup of ``messaging.example.com`` and its own
        runtime already varies 0.76s–1.86s, so a heavy neighbour tips it over.
        PYTHONHASHSEED is forced to a DIFFERENT value than this process's so a
        salted hash cannot coincidentally agree.
        """
        here = {k: schema_hash(*v) for k, v in self.GOLDEN.items()}
        module = _service_root() / "app" / "layer1_domain" / "entities" / "data_reference.py"
        code = (
            "import json,importlib.util as u;"
            f"s=u.spec_from_file_location('dr', {str(module)!r});"
            "m=u.module_from_spec(s); s.loader.exec_module(m);"
            f"g={json.dumps(self.GOLDEN)};"
            "print(json.dumps({k: m.schema_hash(*v) for k, v in g.items()}))"
        )
        env = {**os.environ, "PYTHONHASHSEED": "12345"}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, encoding="utf-8", env=env)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout) == here, (
            "the hash changed across processes — something salted crept in"
        )

    def test_unicode_column_names_hash_deterministically(self):
        a = schema_hash(["tên"], {"tên": "string"})
        b = schema_hash(["tên"], {"tên": "string"})
        assert a == b and a != ""


def _service_root():
    from pathlib import Path
    return Path(__file__).resolve().parents[2]


class TestTheProducersEmitIt:
    """Computed inside the builders, not at the 21 call sites across 9 files.
    A dictionary that is only 95% populated dedupes nothing while looking fine."""

    def test_build_file_ref_carries_the_hash(self):
        ref = build_file_ref(
            file_id="f1", columns=["id"], column_types={"id": "number"})
        assert ref["schema_hash"] == schema_hash(["id"], {"id": "number"})

    def test_build_file_ref_without_a_schema_carries_empty(self):
        assert build_file_ref(file_id="f1")["schema_hash"] == ""

    def test_add_artifact_carries_the_hash(self):
        ref = build_file_ref(file_id="root")
        art = add_artifact(ref, file_id="child", name="lines",
                           column_types={"a": "string"})
        assert art["schema_hash"] == schema_hash(None, {"a": "string"})

    def test_root_and_artifact_share_one_dictionary(self):
        """Same `column_types`, no `columns` on either — one row, not two."""
        root = build_file_ref(file_id="r", column_types={"a": "string"})
        ref = build_file_ref(file_id="r2")
        art = add_artifact(ref, file_id="c", name="lines",
                           column_types={"a": "string"})
        assert root["schema_hash"] == art["schema_hash"] != ""


class TestItActuallyDedupes:
    def test_many_refs_one_schema(self):
        """The shape the dictionary is for: N node outputs of the same workflow
        step share one schema row."""
        cols = ["id", "qty", "sku"]
        types = {"id": "number", "qty": "number", "sku": "string"}
        hashes = {
            build_file_ref(file_id=f"file-{i}", row_count=i,
                           columns=list(cols), column_types=dict(types),
                           value_kind="array", item_kind="object")["schema_hash"]
            for i in range(200)
        }
        assert len(hashes) == 1, "200 refs on one schema must collapse to 1 row"

    def test_different_schemas_do_not_collide(self):
        hashes = {
            build_file_ref(file_id="f", columns=[f"c{i}"],
                           column_types={f"c{i}": "string"})["schema_hash"]
            for i in range(200)
        }
        assert len(hashes) == 200
