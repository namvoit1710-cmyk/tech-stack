"""Generic HANA repository: Prisma-shape operations -> HANA SQL (SA-1488).

Driven by a ``TableSpec`` (table name, primary key, which columns are
JSON/array NCLOB, which are BOOLEAN) so one implementation serves every model.
Synchronous (hdbcli is blocking); the async ``.db`` handle in ``client.py``
wraps these via ``asyncio.to_thread``.

Supported ``where`` today: equality + ``None`` -> ``IS NULL`` (AND-combined).
Prisma operator maps (gt/lt/in/contains) are a documented follow-up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .records import HanaRecord

_log = logging.getLogger("litellm.proxy.db.hana")


def _invalid_column_name(exc: Exception) -> Optional[str]:
    """Return the offending column (lowercased) from a HANA 260 error, else None.
    HANA: "invalid column name: CREATED_AT: line 1 col ...\""""
    s = str(exc)
    if "invalid column name" in s.lower():
        m = re.search(r"invalid column name:\s*([A-Za-z0-9_]+)", s, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return None


# Audit / analytics / archival tables that aren't part of the core HANA schema.
# A create against a *missing* one of these degrades to a no-op (the primary op,
# e.g. the hard delete an archival precedes, still completes). A create against
# any other missing table raises honestly rather than fake-succeeding.
_OPTIONAL_TABLE_MARKERS = ("Deleted", "Deprecated", "Daily", "ErrorLogs", "AuditLog", "Notification")


def _is_optional_table(name: str) -> bool:
    return any(marker in name for marker in _OPTIONAL_TABLE_MARKERS)


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _is_missing_table(e: Exception) -> bool:
    """HANA raises 'invalid table name' when a table does not exist. Optional
    Prisma tables aren't all created up front, so reads against a missing table
    return empty instead of crashing the request (SA-1488)."""
    msg = str(e).lower()
    return "invalid table name" in msg or "cannot find table" in msg


# Prisma String[] columns default to [] (not null); everything else JSON defaults
# to {}. Names are consistent across the LiteLLM schema, so a global set lets the
# generic repository return the right default for a NULL cell (matches Prisma,
# so pydantic validation of the model doesn't fail on None).
_ARRAY_COLUMNS = frozenset(
    {
        "models",
        "teams",
        "admins",
        "members",
        "allowed_cache_controls",
        "allowed_routes",
        "policies",
        "access_group_ids",
        "team_member_permissions",
        "default_team_member_models",
        "allowed_models",
        "mcp_servers",
        "mcp_access_groups",
        "vector_stores",
        "agents",
        "agent_access_groups",
        "blocked_tools",
        "mcp_toolsets",
        "search_tools",
        "request_tags",
        "extra_headers",
        "budget_limits",
    }
)

# Prisma per-field filter operators; anything else in a nested dict is treated as
# a compound-key / relation filter and expanded into column equalities.
_FILTER_OPS = frozenset(
    {
        "in",
        "not_in",
        "notin",
        "not",
        "gt",
        "gte",
        "lt",
        "lte",
        "contains",
        "startswith",
        "starts_with",
        "endswith",
        "ends_with",
        "equals",
    }
)


@dataclass(frozen=True)
class TableSpec:
    name: str
    pk: str
    json_cols: frozenset = frozenset()
    bool_cols: frozenset = frozenset()
    auto_uuid: frozenset = frozenset()  # cols with Prisma @default(uuid()) — generated on create if missing


class HanaRepository:
    def __init__(self, mgr: Any, spec: TableSpec):
        self.mgr = mgr
        self.spec = spec

    # --- encode/decode across the JSON/array NCLOB boundary ---
    def _encode(self, data: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in data.items():
            if k in self.spec.json_cols:
                if v is None or v == "null":
                    # JSON null -> SQL NULL, matching Postgres (so `IS NOT NULL`
                    # filters skip it instead of returning the literal "null").
                    out[k] = None
                elif not isinstance(v, str):
                    out[k] = json.dumps(v)
                else:
                    out[k] = v
            else:
                out[k] = v
        return out

    def _decode_row(self, cols: List[str], row) -> HanaRecord:
        rec = HanaRecord()
        for name, val in zip(cols, row):
            if name in self.spec.json_cols:
                if val is None:
                    # NULL JSON/array cell -> Prisma default ([] for String[], else {})
                    val = [] if name in _ARRAY_COLUMNS else {}
                elif isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (ValueError, TypeError):
                        pass
            elif val is not None and name in self.spec.bool_cols:
                val = bool(val)
            rec[name] = val
        return rec

    def _op_clause(self, col: str, op: str, val: Any, insensitive: bool = False):
        """Translate one Prisma filter operator to a SQL clause + params.

        ``insensitive`` mirrors Prisma's ``mode: "insensitive"`` hint: HANA
        column collation is not guaranteed case-insensitive (unlike Postgres),
        so an ``equals`` comparison is done via ``UPPER()`` on both sides
        instead of assuming the column collation matches.
        """
        q = _q(col)
        o = op.lower()
        if o == "in":
            vals = list(val) if val is not None else []
            return (f"{q} IN ({', '.join('?' for _ in vals)})", vals) if vals else ("1 = 0", [])
        if o in ("not_in", "notin"):
            vals = list(val) if val is not None else []
            return (f"{q} NOT IN ({', '.join('?' for _ in vals)})", vals) if vals else ("1 = 1", [])
        if o == "not":
            if val is None:
                return f"{q} IS NOT NULL", []
            if isinstance(val, dict):
                # Prisma nested negation, e.g. {"not": {"in": [...]}} -> NOT (col IN (...)).
                subs, sub_params = [], []
                for sop, sval in val.items():
                    if isinstance(sop, str) and sop.lower() in _FILTER_OPS:
                        c, p = self._op_clause(col, sop, sval)
                        subs.append(c)
                        sub_params.extend(p)
                if subs:
                    return "NOT (" + " AND ".join(subs) + ")", sub_params
                return "1 = 1", []
            return f"{q} <> ?", [val]
        if o in ("gt", "gte", "lt", "lte"):
            sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[o]
            return f"{q} {sym} ?", [val]
        if o == "contains":
            return f"{q} LIKE ?", [f"%{val}%"]
        if o in ("startswith", "starts_with"):
            return f"{q} LIKE ?", [f"{val}%"]
        if o in ("endswith", "ends_with"):
            return f"{q} LIKE ?", [f"%{val}"]
        if o == "equals":
            if val is None:
                return f"{q} IS NULL", []
            if insensitive:
                return f"UPPER({q}) = UPPER(?)", [val]
            return f"{q} = ?", [val]
        return f"{q} = ?", [val]  # unknown operator -> best-effort equality

    def _build_where(self, where: Optional[Dict[str, Any]]):
        """Recursive WHERE body (no leading WHERE) + params. Supports Prisma
        logical operators OR/AND/NOT and per-field operator dicts."""
        if not where:
            return "", []
        clauses, params = [], []
        for k, v in where.items():
            ku = k.upper() if isinstance(k, str) else ""
            if ku in ("OR", "AND") and isinstance(v, list):
                subs = [self._build_where(c) for c in v]
                subs = [(s, p) for s, p in subs if s]
                if subs:
                    joiner = " OR " if ku == "OR" else " AND "
                    clauses.append("(" + joiner.join(s for s, _ in subs) + ")")
                    for _, p in subs:
                        params.extend(p)
            elif ku == "NOT" and isinstance(v, (dict, list)):
                items = v if isinstance(v, list) else [v]
                subs = [(s, p) for s, p in (self._build_where(c) for c in items) if s]
                if subs:
                    clauses.append("NOT (" + " AND ".join(s for s, _ in subs) + ")")
                    for _, p in subs:
                        params.extend(p)
            elif v is None:
                clauses.append(f"{_q(k)} IS NULL")
            elif isinstance(v, dict):
                if v and any(isinstance(kk, str) and kk.lower() in _FILTER_OPS for kk in v):
                    # per-field operator filter, e.g. {"team_id": {"in": [...]}};
                    # honor Prisma's {"mode": "insensitive"} hint instead of
                    # silently dropping it (HANA collation isn't guaranteed
                    # case-insensitive the way Postgres's is).
                    insensitive = str(v.get("mode", "")).lower() == "insensitive"
                    for op, val in v.items():
                        if isinstance(op, str) and op.lower() in _FILTER_OPS:
                            c, p = self._op_clause(k, op, val, insensitive=insensitive)
                            clauses.append(c)
                            params.extend(p)
                else:
                    # Prisma compound-key / nested filter, e.g.
                    # {"user_id_team_id": {"user_id": x, "team_id": y}} -> (user_id=? AND team_id=?)
                    sub, sp = self._build_where(v)
                    if sub:
                        clauses.append("(" + sub + ")")
                        params.extend(sp)
            else:
                clauses.append(f"{_q(k)} = ?")
                params.append(v)
        return " AND ".join(clauses), params

    def _where_sql(self, where: Optional[Dict[str, Any]]):
        body, params = self._build_where(where)
        return ((" WHERE " + body) if body else ""), params

    def _exec(self, cur, sql, params):
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)

    # --- Prisma-shape operations ---
    def find_many(self, where=None, order=None, take=None, skip=None) -> List[HanaRecord]:
        wsql, params = self._where_sql(where)
        sql = f"SELECT * FROM {_q(self.spec.name)}{wsql}"
        if order:
            items = order if isinstance(order, list) else [order]
            ob = [
                f"{_q(col)} {'DESC' if str(dirn).lower() == 'desc' else 'ASC'}"
                for o in items
                for col, dirn in o.items()
            ]
            if ob:
                sql += " ORDER BY " + ", ".join(ob)
        if take is not None:
            sql += f" LIMIT {int(take)}"
        if skip is not None:
            sql += f" OFFSET {int(skip)}"
        try:
            with self.mgr.cursor() as cur:
                self._exec(cur, sql, params)
                cols = [d[0] for d in cur.description]
                return [self._decode_row(cols, r) for r in cur.fetchall()]
        except Exception as e:
            if _is_missing_table(e):
                return []
            raise

    def find_first(self, where=None, order=None) -> Optional[HanaRecord]:
        rows = self.find_many(where=where, order=order, take=1)
        return rows[0] if rows else None

    def find_unique(self, where=None) -> Optional[HanaRecord]:
        return self.find_first(where=where)

    def create(self, data: Dict[str, Any]) -> HanaRecord:
        import uuid
        from datetime import datetime, timezone

        data = dict(data)
        for field in self.spec.auto_uuid:
            if not data.get(field):
                data[field] = str(uuid.uuid4())
        # Emulate Prisma @default(now())/@updatedAt: populate the conventional
        # timestamp columns when the caller relied on DB-side defaults (otherwise
        # they insert NULL and callers doing created_at.isoformat() crash).
        ts_added = []
        present = {k.lower() for k in data}
        for tcol in ("created_at", "updated_at"):
            if tcol not in present:
                data[tcol] = datetime.now(timezone.utc)
                ts_added.append(tcol)
        while True:
            enc = self._encode(data)
            cols = list(enc.keys())
            sql = (
                f"INSERT INTO {_q(self.spec.name)} ({', '.join(_q(c) for c in cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})"
            )
            try:
                with self.mgr.cursor() as cur:
                    cur.execute(sql, [enc[c] for c in cols])
                break
            except Exception as e:
                bad = _invalid_column_name(e)
                if bad is not None and bad in ts_added:
                    # This table has no such timestamp column; drop it and retry.
                    data.pop(bad, None)
                    ts_added.remove(bad)
                    continue
                if _is_missing_table(e):
                    if _is_optional_table(self.spec.name):
                        _log.warning(
                            "HANA create: optional table %r absent; skipping insert "
                            "(audit/analytics not persisted). %s",
                            self.spec.name,
                            e,
                        )
                        return HanaRecord(data)
                    # A primary table is genuinely missing — fail honestly rather than
                    # returning a fake "created" record the caller can't read back.
                    raise
                raise
        if self.spec.pk in data:
            got = self.find_unique({self.spec.pk: data[self.spec.pk]})
            if got is not None:
                return got
        return HanaRecord(data)

    def _build_set(self, data: Dict[str, Any]):
        """SET fragments + params, honoring Prisma atomic ops {"increment"/"decrement": n}."""
        frags, params = [], []
        for k, v in (data or {}).items():
            if isinstance(v, dict) and ("increment" in v or "decrement" in v):
                if "increment" in v:
                    frags.append(f"{_q(k)} = {_q(k)} + ?")
                    params.append(v["increment"])
                else:
                    frags.append(f"{_q(k)} = {_q(k)} - ?")
                    params.append(v["decrement"])
            else:
                if k in self.spec.json_cols:
                    if v == "null":
                        v = None  # JSON null -> SQL NULL (see _encode)
                    elif v is not None and not isinstance(v, str):
                        v = json.dumps(v)
                frags.append(f"{_q(k)} = ?")
                params.append(v)
        return frags, params

    def _apply_array_ops(self, where, data):
        """Resolve Prisma array update ops to a concrete list SET (HANA has no
        native JSON array ops): {"col": {"set": v}} replaces; {"col": {"push": v}}
        appends via read-modify-write."""
        if not data:
            return data
        ops = {
            k: v
            for k, v in data.items()
            if isinstance(v, dict) and (set(v.keys()) == {"set"} or set(v.keys()) == {"push"})
        }
        if not ops:
            return data
        out = dict(data)
        current = None  # lazily fetched only for push (set needs no prior value)
        for k, v in ops.items():
            if "set" in v:
                val = v["set"]
                out[k] = list(val) if isinstance(val, (list, tuple)) else [val]
                continue
            add = v["push"]
            add_list = add if isinstance(add, list) else [add]
            if current is None:
                current = self.find_first(where=where)
            existing: List[Any] = []
            if current is not None:
                cur_val = current.get(k)
                if isinstance(cur_val, list):
                    existing = list(cur_val)
                elif isinstance(cur_val, str) and cur_val.strip():
                    try:
                        parsed = json.loads(cur_val)
                        existing = parsed if isinstance(parsed, list) else []
                    except (ValueError, TypeError):
                        existing = []
            out[k] = existing + add_list
        return out

    def update(self, where: Dict[str, Any], data: Dict[str, Any]) -> Optional[HanaRecord]:
        data = self._apply_array_ops(where, data)
        frags, sparams = self._build_set(data or {})
        if not frags:
            return self.find_first(where=where)  # no-op update -> return existing row
        wsql, wparams = self._where_sql(where)
        sql = f"UPDATE {_q(self.spec.name)} SET {', '.join(frags)}{wsql}"
        with self.mgr.cursor() as cur:
            self._exec(cur, sql, sparams + wparams)
        return self.find_first(where=where)

    def update_many(self, where: Dict[str, Any], data: Dict[str, Any]) -> int:
        """UPDATE that tolerates a missing row (Prisma update_many); returns affected count."""
        data = self._apply_array_ops(where, data)
        frags, sparams = self._build_set(data or {})
        if not frags:
            return 0
        wsql, wparams = self._where_sql(where)
        sql = f"UPDATE {_q(self.spec.name)} SET {', '.join(frags)}{wsql}"
        try:
            with self.mgr.cursor() as cur:
                self._exec(cur, sql, sparams + wparams)
                return getattr(cur, "rowcount", -1)
        except Exception as e:
            if _is_missing_table(e):
                return 0
            raise

    def upsert(self, where, create, update) -> Optional[HanaRecord]:
        if self.find_unique(where=where) is not None:
            return self.update(where, update)
        return self.create(create)

    def group_by(
        self, by=None, where=None, having=None, order=None, sum=None, count=None, min=None, max=None, avg=None, **kwargs
    ):
        """Prisma aggregation -> SQL GROUP BY. Returns rows with the group keys
        flat and each aggregate nested under _sum/_count/_min/_max/_avg."""
        group_cols = list(by or [])
        select_parts = [_q(c) for c in group_cols]
        plan = []  # (out_key, member_col, result-set alias)

        def _add(spec, func, out_key):
            if not spec:
                return
            if spec is True:  # e.g. count=True -> COUNT(*) under _all
                alias = out_key + "_all"
                select_parts.append('COUNT(*) AS "{}"'.format(alias))
                plan.append((out_key, "_all", alias))
            elif isinstance(spec, dict):
                for col, on in spec.items():
                    if on:
                        alias = out_key + "_" + col
                        select_parts.append('{}({}) AS "{}"'.format(func, _q(col), alias))
                        plan.append((out_key, col, alias))

        _add(sum, "SUM", "_sum")
        _add(count, "COUNT", "_count")
        _add(min, "MIN", "_min")
        _add(max, "MAX", "_max")
        _add(avg, "AVG", "_avg")
        if not select_parts:
            return []
        wsql, params = self._where_sql(where)
        sql = "SELECT {} FROM {}{}".format(", ".join(select_parts), _q(self.spec.name), wsql)
        if group_cols:
            sql += " GROUP BY " + ", ".join(_q(c) for c in group_cols)
        try:
            with self.mgr.cursor() as cur:
                self._exec(cur, sql, params)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
        except Exception as e:
            if _is_missing_table(e):
                return []
            raise
        out: List[HanaRecord] = []
        for r in rows:
            row = dict(zip(cols, r))
            rec = HanaRecord()
            for c in group_cols:
                rec[c] = row.get(c)
            for out_key, member_col, alias in plan:
                rec.setdefault(out_key, {})[member_col] = row.get(alias)
            out.append(rec)
        return out

    def create_many(self, rows, skip_duplicates: bool = False) -> int:
        count = 0
        for row in rows:
            try:
                self.create(dict(row))
                count += 1
            except Exception as e:
                msg = str(e).lower()
                if skip_duplicates and ("unique constraint" in msg or "duplicate" in msg or " 301" in msg):
                    continue
                raise
        return count

    def delete(self, where) -> Optional[HanaRecord]:
        rec = self.find_unique(where=where)
        wsql, params = self._where_sql(where)
        with self.mgr.cursor() as cur:
            self._exec(cur, f"DELETE FROM {_q(self.spec.name)}{wsql}", params)
        return rec

    def delete_many(self, where=None) -> int:
        wsql, params = self._where_sql(where)
        with self.mgr.cursor() as cur:
            self._exec(cur, f"DELETE FROM {_q(self.spec.name)}{wsql}", params)
            return getattr(cur, "rowcount", -1)

    def count(self, where=None) -> int:
        wsql, params = self._where_sql(where)
        try:
            with self.mgr.cursor() as cur:
                self._exec(cur, f"SELECT COUNT(*) FROM {_q(self.spec.name)}{wsql}", params)
                return int(cur.fetchone()[0])
        except Exception as e:
            if _is_missing_table(e):
                return 0
            raise
