"""Every runnable ART41.00 rule, against a deployed tenant, in one bundle call.

    set ART41_WORKBOOK_DIR=C:\\path\\holding\\the\\two\\xlsx
    python tests/e2e/art41/run_all_rules.py

16 of the 18 assigned rules run. The two that do not are named and explained
rather than quietly skipped.

The expected result is computed from the two workbooks before the server is
called, and asserted per rule by ItemID. Counts can agree by accident; twelve
distinct ItemID sets cannot. If this prints anything other than a clean score,
the difference is between what the workbook says and what the service did.

Uploads are deleted at the end, including the ones the service produced.
"""

import csv
import io
import json
import os
import sys

import fastexcel
import polars as pl
import requests

from rule_catalogue import (RULES, VALID, INVALID, ITEM, FILE_SERVICE,
                            DATA_FACTORY, permitted_pairs)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

trash, checks = [], []


def record(label, ok, detail=""):
    checks.append((label, ok))
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}")
    if detail:
        print(f"         {detail}")


def upload(name, data, content_type):
    file_id = requests.post(f"{FILE_SERVICE}/api/v1/upload",
                            files={"file": (name, data, content_type)},
                            timeout=600).json()["file_id"]
    trash.append(file_id)
    return file_id


def violations_of(rule, pairs, invalid_reader):
    """Which ItemIDs the invalid workbook should trip this rule on."""
    sheet = rule["sheet"]
    frame = invalid_reader.load_sheet(sheet, header_row=2).to_polars()

    if rule["kind"] == "length":
        column = rule["target"]
        hits = frame.filter(
            pl.col(column).cast(pl.Utf8).str.len_chars() > rule["params"]["max"])
        return sorted(int(v) for v in hits[ITEM].to_list())

    if rule["kind"] == "derive":
        condition = (invalid_reader.load_sheet(rule["join_from"], header_row=2)
                     .to_polars().select([ITEM, rule["source"]]))
        joined = frame.join(condition, on=ITEM, how="left")
        changed = joined.filter(
            (pl.col(rule["source"]) == rule["when_value"])
            & (pl.col(rule["target"]).cast(pl.Utf8) != rule["then_value"]))
        return sorted(int(v) for v in changed[ITEM].to_list())

    source, target = rule["source"], rule["target"]
    if rule.get("join_from"):
        borrowed = (invalid_reader.load_sheet(rule["join_from"], header_row=2)
                    .to_polars().select([ITEM, source]))
        frame = frame.select([ITEM, target]).join(borrowed, on=ITEM, how="left")

    allowed = set(zip(pairs[source].cast(pl.Utf8).to_list(),
                      pairs[target].cast(pl.Utf8).to_list()))
    known = {s for s, _ in allowed}
    flagged = []
    for item, s, t in zip(frame[ITEM].to_list(),
                          frame[source].cast(pl.Utf8).to_list(),
                          frame[target].cast(pl.Utf8).to_list()):
        if s in known and t is not None and (s, t) not in allowed:
            flagged.append(int(item))
    return sorted(flagged)


def main():
    if not os.environ.get("ART41_WORKBOOK_DIR") or not os.path.isfile(INVALID):
        print("Set ART41_WORKBOOK_DIR to the folder holding both ART41.00 "
              f"workbooks. Looked for:\n  {VALID}\n  {INVALID}")
        return 2

    print("=" * 78)
    print("ALL RUNNABLE ART41.00 RULES")
    print(f"  file service: {FILE_SERVICE}")
    print(f"  data factory: {DATA_FACTORY}")
    print("=" * 78)

    valid = fastexcel.read_excel(VALID)
    invalid = fastexcel.read_excel(INVALID)

    # ------------------------------------------ 1. mappings, mined and uploaded
    print("\nmining the value mappings from the valid workbook")
    oracle, mapping_ids = {}, {}
    for rule in RULES:
        if rule["kind"] == "-":
            continue
        pairs = None
        if rule["kind"] in {"filter", "assignment"}:
            pairs = permitted_pairs(valid, rule)
            if pairs is None or pairs.is_empty():
                print(f"  !! {rule['id']}: no mapping could be mined")
                continue
            mapping_ids[rule["id"]] = upload(
                f"map-{rule['id']}.csv",
                io.BytesIO(pairs.write_csv().encode()), "text/csv")
            # One target per source is a derivation the rule can check exactly.
            # Several is a permitted list. The mapping says which, so the config
            # does not have to guess.
            rule["mode"] = ("match" if pairs.height == pairs[rule["source"]].n_unique()
                            else "allowed")
            print(f"  {rule['id']:<28} {pairs.height:>3} pairs, mode={rule['mode']}")
        oracle[rule["id"]] = violations_of(rule, pairs, invalid)

    with open(INVALID, "rb") as handle:
        book = upload("ART41.00.xlsx", handle,
                      "application/vnd.openxmlformats-officedocument."
                      "spreadsheetml.sheet")

    # ------------------------------------------------------ 2. build the bundle
    by_sheet = {}
    for rule in RULES:
        if rule["kind"] != "-":
            by_sheet.setdefault(rule["sheet"], []).append(rule)
    # ArticleMaster first: rules on other sheets join back to its output.
    order = ["ArticleMaster"] + [s for s in by_sheet if s != "ArticleMaster"]

    tables = []
    for sheet in order:
        here = by_sheet[sheet]
        transform, validation, joins = [], [], {}

        for rule in here:
            if rule["kind"] == "derive":
                joins[rule["source"]] = rule["join_from"]
                transform.append({"type": "derive", "params": {
                    "when": {"column": rule["source"], "operator": "EQUAL",
                             "value": rule["when_value"]},
                    "then": [{"column": rule["target"],
                              "value": rule["then_value"]}]}})
            elif rule["kind"] == "length":
                validation.append({
                    "rule_name": rule["id"], "type": "length",
                    "error_message": rule["why"],
                    "params": {"columns": [rule["target"]],
                               "max": rule["params"]["max"]}})
            elif rule["id"] in mapping_ids:
                if rule.get("join_from"):
                    joins[rule["source"]] = rule["join_from"]
                params = {"source_column": rule["source"],
                          "target_column": rule["target"],
                          "mapping_source": mapping_ids[rule["id"]],
                          "mapping_format": "csv"}
                if rule["kind"] == "assignment":
                    params["mode"] = rule.get("mode", "match")
                else:
                    params["severity"] = "error"
                validation.append({"rule_name": rule["id"], "type": rule["kind"],
                                   "error_message": rule["why"], "params": params})

        # A borrowed column is joined in first. It may only be dropped again if
        # no validation rule reads it: validation runs on the *output*, so
        # dropping a column it needs takes it away before the rule ever runs.
        needed = ({v["params"].get("source_column") for v in validation}
                  | {v["params"].get("target_column") for v in validation})
        pre = [{"type": "join_reference",
                "params": {"table": src, "file_format": "csv", "on": [ITEM],
                           "columns": [col], "how": "left"}}
               for col, src in joins.items()]
        droppable = [c for c in joins if c not in needed]
        post = ([{"type": "drop_columns", "params": {"columns": droppable}}]
                if droppable else [])

        # Pins belong to the table that uses them: `Supplier (String)` is
        # PURCHASING.supplier here and SALESPRICE.supplier on the next sheet.
        pins = {}
        for rule in here:
            if rule.get("target") and rule.get("path"):
                pins[rule["target"]] = rule["path"]
            if rule.get("source") and rule.get("source_path"):
                pins[rule["source"]] = rule["source_path"]

        tables.append({
            "name": sheet, "sheet_names": [sheet], "header_row": 2,
            "field_path_overrides": pins,
            "depends_on": ["ArticleMaster"] if joins else [],
            "rules": pre + transform + post + [
                {"type": "add_technical_fields",
                 "params": {"object_id": "OBJ-ART41", "table_name": sheet,
                            "source_row": "sourceRow", "source_row_offset": 4}}],
            "validation_rules": validation,
        })

    print(f"\nbundle: {len(tables)} tables, "
          f"{sum(len(t['validation_rules']) for t in tables)} validation rules, "
          f"{sum(1 for t in tables for r in t['rules'] if r['type'] == 'derive')} derivation")

    response = requests.post(
        f"{DATA_FACTORY}/api/v1/bundle/transform-validate",
        json={"source_file_id": book, "file_format": "xlsx",
              "output_format": "csv", "default_header_row": 2, "tables": tables},
        timeout=2400)
    body = response.json() if "json" in response.headers.get("content-type", "") else {}
    got = {t.get("name"): t for t in (body.get("tables") or [])}
    for table in got.values():
        trash.append(table.get("output_file_id"))
        trash.append((table.get("validation") or {}).get("result_file_id"))

    print()
    record("bundle returns 200", response.status_code == 200,
           f"HTTP {response.status_code}" if response.status_code != 200 else "")
    # 200 does not mean every table succeeded: the bundle reports per table.
    record("every table ran", bool(got) and all(t.get("success") for t in got.values()),
           ", ".join(f"{n}={t.get('success')}" for n, t in got.items()
                     if not t.get("success")) or "all ok")

    # ---------------------------------------------------------- 3. score it
    flagged, seen_error = {}, {}
    for table in got.values():
        url = (table.get("validation") or {}).get("result_file_url")
        if not url:
            continue
        got_result = requests.get(url, timeout=300)
        if got_result.status_code != 200:
            continue
        for row in csv.DictReader(io.StringIO(got_result.text)):
            try:
                errors = json.loads(row.get("validation_errors") or "[]")
            except ValueError:
                continue
            for error in errors:
                flagged.setdefault(error.get("rule"), []).append(int(float(row[ITEM])))
                seen_error[error.get("rule")] = error

    print("\nper-rule result")
    for rule in RULES:
        if rule["kind"] == "-":
            print(f"  [n/a ] {rule['id']:<28} {rule['why']}")
            continue
        if rule["kind"] == "derive":
            continue
        expected = oracle.get(rule["id"], [])
        actual = sorted(set(flagged.get(rule["id"], [])))
        record(f"{rule['id']} flags {expected or 'nothing'}", actual == expected,
               f"got {actual}" if actual != expected else "")

    # The derivation changes data rather than reporting it, so it is read off
    # the transformed table instead of the validation result.
    derive = next((r for r in RULES if r["kind"] == "derive"), None)
    if derive:
        table = got.get(derive["sheet"], {})
        rows = []
        if table.get("output_file_id"):
            out = requests.get(
                f"{FILE_SERVICE}/api/v1/download/{table['output_file_id']}",
                timeout=300)
            if out.status_code == 200:
                rows = list(csv.DictReader(io.StringIO(out.text)))
        derived = {int(float(r[ITEM])): r.get(derive["target"])
                   for r in rows if r.get(ITEM)}
        want = oracle.get(derive["id"], [])
        record(f"{derive['id']} rewrote {want} to {derive['then_value']}",
               bool(derived) and all(derived.get(i) == derive["then_value"]
                                     for i in want),
               ", ".join(f"{i}={derived.get(i)!r}" for i in want)
               if derived else "no output rows")
        record("the joined condition column did not leak into the output",
               bool(rows) and derive["source"] not in rows[0])

    print("\nthe path each violation reports")
    for rule_id, error in sorted(seen_error.items()):
        rule = next((r for r in RULES if r["id"] == rule_id), {})
        record(f"{rule_id} reports {rule.get('path')}",
               error.get("path") == rule.get("path"),
               f"got {error.get('path')!r}")
        if error.get("ruleType") == "FILTER":
            record(f"{rule_id} names its source {rule.get('source_path')}",
                   error.get("sourcePath") == rule.get("source_path"),
                   f"got {error.get('sourcePath')!r}")

    print()
    deleted = 0
    for file_id in dict.fromkeys(f for f in trash if f):
        try:
            if requests.delete(f"{FILE_SERVICE}/api/v1/files/{file_id}",
                               timeout=120).status_code < 400:
                deleted += 1
        except Exception:
            pass
    print(f"cleanup: deleted {deleted} of {len({f for f in trash if f})} files")

    passed = sum(1 for _, ok in checks if ok)
    print()
    print("=" * 78)
    print(f"RESULT: {passed}/{len(checks)} as expected")
    for label, ok in checks:
        if not ok:
            print(f"  FAILED: {label}")
    print("=" * 78)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
