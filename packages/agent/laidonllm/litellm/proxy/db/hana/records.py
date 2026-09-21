"""Lightweight record type for HANA rows (SA-1488).

LiteLLM code accesses Prisma results both by attribute (``row.token``) and as
dicts. ``HanaRecord`` is a dict that also supports attribute access + the
``model_dump``/``dict`` shims Prisma models expose, so it is a drop-in for the
call sites that consume ``prisma_client.db.<model>.<op>()`` results.
"""
from __future__ import annotations

import json as _json


class HanaRecord(dict):
    def __getattr__(self, key):
        if key in self:
            return self[key]
        # Dunders must still raise so Python protocols / pydantic internals work.
        if key.startswith("__") and key.endswith("__"):
            raise AttributeError(key)
        # Prisma returns None for unset scalar fields and un-included relations
        # (e.g. organization_memberships, litellm_budget_table, object_permission).
        # Our flat HANA row omits those, so mirror Prisma: absent -> None, not error.
        return None

    def __setattr__(self, key, value):
        self[key] = value

    def model_dump(self, *args, **kwargs):
        return dict(self)

    def dict(self, *args, **kwargs):
        return dict(self)

    def json(self, *args, **kwargs):
        return _json.dumps(self, default=str)

    def model_dump_json(self, *args, **kwargs):
        # Pydantic v2 shim: JSON string of the record. Defined explicitly so it does
        # not fall through __getattr__ (which returns None -> None(...) -> TypeError).
        return _json.dumps(dict(self), default=str)

    def model_copy(self, *args, update=None, **kwargs):
        new = HanaRecord(self)
        if update:
            new.update(update)
        return new
