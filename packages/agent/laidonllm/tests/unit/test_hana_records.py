"""Tests for litellm/proxy/db/hana/records.py (SA-2237).

Lives outside the litellm package for the same collection-import reason as
test_hana_config.py (see that file's docstring) — records.py has no
package-relative imports so it loads trivially by file path.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "litellm", "proxy", "db", "hana")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_BASE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


records = _load("hana_records", "records.py")
HanaRecord = records.HanaRecord


# --- dict/attribute duality -----------------------------------------------


def test_attribute_read_reflects_dict_item():
    rec = HanaRecord(token="sk-1")
    assert rec.token == "sk-1"
    assert rec["token"] == "sk-1"


def test_attribute_write_reflects_as_dict_item():
    rec = HanaRecord()
    rec.token = "sk-2"
    assert rec["token"] == "sk-2"
    assert rec.token == "sk-2"


# --- R3: unknown field returns None (Prisma-mirroring), by design ---------


def test_unknown_field_returns_none_not_raises():
    rec = HanaRecord(token="sk-1")
    assert rec.organization_memberships is None


def test_dunder_lookup_still_raises_attribute_error():
    rec = HanaRecord()
    with pytest.raises(AttributeError):
        rec.__nonexistent_dunder__


# --- pydantic/Prisma shims --------------------------------------------------


def test_model_dump_returns_plain_dict():
    rec = HanaRecord(token="sk-1", spend=1.5)
    dumped = rec.model_dump()
    assert dumped == {"token": "sk-1", "spend": 1.5}
    assert type(dumped) is dict


def test_dict_shim_returns_plain_dict():
    rec = HanaRecord(token="sk-1")
    assert rec.dict() == {"token": "sk-1"}
    assert type(rec.dict()) is dict


def test_json_shim_round_trips():
    import json

    rec = HanaRecord(token="sk-1", spend=1.5)
    assert json.loads(rec.json()) == {"token": "sk-1", "spend": 1.5}


def test_model_dump_json_round_trips():
    import json

    rec = HanaRecord(token="sk-1")
    assert json.loads(rec.model_dump_json()) == {"token": "sk-1"}


def test_model_copy_without_update_is_equal_but_distinct():
    rec = HanaRecord(token="sk-1")
    copy = rec.model_copy()
    assert copy == rec
    assert copy is not rec
    assert isinstance(copy, HanaRecord)


def test_model_copy_with_update_overrides_fields():
    rec = HanaRecord(token="sk-1", spend=0)
    copy = rec.model_copy(update={"spend": 5})
    assert copy.spend == 5
    assert rec.spend == 0  # original untouched
