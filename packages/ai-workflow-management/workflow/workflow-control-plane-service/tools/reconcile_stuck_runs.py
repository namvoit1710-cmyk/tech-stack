"""Move abandoned non-terminal runs to a terminal state.

Why this exists
---------------
Measured on tenant-1 on 2026-08-01: 339 of 5430 runs (6.2%) had never reached
a terminal state.

    RUNNING    74   oldest 30 days, none younger than 1 hour
    PENDING   141
    CREATED   124   oldest 31.2 days

45 of their tasks sat in RUNNING — dispatched to a worker and never answered
for between 1 and 30 days. They span eight-plus distinct workflows, so this is
not one bad definition; it is that nothing reconciles a run whose driver died.

This matters beyond tidiness. **Every per-run resource is released on a
TERMINAL transition and only there** — the resolver caches, the three
projector view-store dicts, the per-run ``asyncio.Lock``. A run that never
terminates never releases, which is why ``MAX_RUN_LOCKS`` needed a size
backstop (SA-2254). 339 stuck runs is a third of that 1024 cap, on a tenant
doing no work at all.

Why this drives the API and not SQL
-----------------------------------
The first version of this tool issued ``UPDATE AIW_WORKFLOW_RUNS SET STATUS``.
That would have been wrong in a way the dry run could not show.

Run status has been event-sourced since Phase 3b.2c. ``ProjectedView.status``
returns ``self._projected.status`` — folded from the event log — and
``read_authoritative_status`` prefers it. Writing the row directly moves one
source of truth and leaves the other folding the old value, so the run would
read RUNNING through the API and FAILED in the table, forever. Two disagreeing
answers is worse than one stuck one.

So: SQL is used only to FIND the runs. The transition goes through
``POST /runs/{id}/cancel``, which emits ``RunCancelled``, cancels the run's
non-terminal tasks, and lets the projector rebuild — the same path a user
clicking Cancel takes.

What this does NOT do
---------------------
It does not fix the cause. A dispatched task can still sit in RUNNING
indefinitely because that path has no dispatch timeout. This is cleanup; the
timeout is separate work.

Safety
------
* **Dry-run is the default.** ``--apply`` is required to change anything.
* Only runs older than ``--older-than-hours`` (default 24) are touched, so a
  slow-but-live run is never killed.
* Every affected id is printed, and the census is re-read afterwards, so the
  change is auditable from the output alone.
* Failures are counted and reported per status rather than aborting the batch:
  a single rejected run should not strand the other 328.

Usage (from inside the pod)
---------------------------
    python3 tools/reconcile_stuck_runs.py
    python3 tools/reconcile_stuck_runs.py --apply
    python3 tools/reconcile_stuck_runs.py --apply --limit 5     # rehearse
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter

sys.path.insert(0, "/app")

NON_TERMINAL = ("RUNNING", "PENDING", "CREATED")

# ``emit_run_status`` writes a verb, not a RunStatus. These are the verbs that
# mean the run is over.
TERMINAL_VERBS = frozenset({"cancelled", "completed", "failed"})


def _connect():
    from app.layer4_frameworks.config.app_config import settings
    from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
        HanaConnectionManager,
    )
    mgr = HanaConnectionManager(settings)
    mgr.initialize()
    return mgr


def row_census(mgr) -> dict[str, int]:
    """What the STATUS column says. Kept only to show how far it has drifted."""
    out: dict[str, int] = {}
    for row in mgr.execute_query(
        'SELECT STATUS AS S, COUNT(*) AS C FROM "AIW_WORKFLOW_RUNS" '
        "GROUP BY STATUS"
    ):
        vals = list(row.values())
        out[str(vals[0])] = int(vals[1])
    return out


def _latest_verbs(mgr) -> dict[str, str]:
    """run_id -> the verb from its most recent ``RunStatusChanged``.

    This is the authoritative status. The first version of this tool used the
    STATUS column for its before/after census and could therefore never show
    any progress at all: a cancel does not write that column, so the numbers
    came back identical after successfully cancelling runs. Measured on
    tenant-1: 88 of 338 rows already disagreed with their own event log, and
    26 runs the column called non-terminal had in fact been cancelled.
    """
    latest: dict[str, tuple[int, str]] = {}
    rows = mgr.execute_query(
        'SELECT RUN_ID, SEQUENCE, PAYLOAD FROM "AIW_EVENT_LOG" '
        "WHERE EVENT_TYPE = 'RunStatusChanged'"
    )
    for row in rows:
        vals = list(row.values())
        run_id, seq, payload = str(vals[0]), int(vals[1] or 0), vals[2]
        if payload is None:
            continue
        if run_id in latest and latest[run_id][0] >= seq:
            continue
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else payload
        except ValueError:
            continue
        verb = str(
            parsed.get("status") or parsed.get("new_status")
            or parsed.get("to") or ""
        ).lower()
        if verb:
            latest[run_id] = (seq, verb)
    return {run_id: verb for run_id, (_seq, verb) in latest.items()}


def authoritative_census(mgr) -> tuple[dict[str, int], dict[str, str]]:
    """(counts, run_id -> authoritative label).

    A run with no events at all falls back to its row, which is what the
    projector itself does.
    """
    verbs = _latest_verbs(mgr)
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for row in mgr.execute_query('SELECT ID, STATUS FROM "AIW_WORKFLOW_RUNS"'):
        vals = list(row.values())
        run_id, row_status = str(vals[0]), str(vals[1])
        label = verbs.get(run_id) or f"{row_status.lower()} (no events)"
        labels[run_id] = label
        counts[label] = counts.get(label, 0) + 1
    return counts, labels


def candidates(
    mgr, older_than_hours: int, labels: dict[str, str],
) -> list[tuple[str, str, float]]:
    """(run_id, authoritative label, age_hours), oldest first.

    Runs the event log already calls terminal are skipped: cancelling them
    returns 409 INVALID_RUN_STATUS, which is correct behaviour and pure noise
    in the output.
    """
    statuses = ", ".join(f"'{s}'" for s in NON_TERMINAL)
    rows = mgr.execute_query(
        'SELECT ID, '
        'SECONDS_BETWEEN(TO_TIMESTAMP(STARTED_AT), CURRENT_TIMESTAMP) AS AGE '
        'FROM "AIW_WORKFLOW_RUNS" '
        f"WHERE STATUS IN ({statuses}) "
        "AND SECONDS_BETWEEN(TO_TIMESTAMP(STARTED_AT), CURRENT_TIMESTAMP) > "
        f"{int(older_than_hours) * 3600} "
        "ORDER BY AGE DESC"
    )
    out = []
    for r in rows:
        vals = list(r.values())
        run_id, age_h = str(vals[0]), float(vals[1]) / 3600.0
        label = labels.get(run_id, "?")
        if label.split()[0] in TERMINAL_VERBS:
            continue
        out.append((run_id, label, age_h))
    return out


def cancel(base_url: str, run_id: str, timeout: float) -> tuple[bool, str]:
    """POST /runs/{id}/cancel. Returns (ok, detail)."""
    req = urllib.request.Request(
        f"{base_url}/api/v1/runs/{run_id}/cancel",
        method="POST",
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"
    # The endpoint returns 200 with an error_code in the body on a rejected
    # transition — a 200 is not by itself success here.
    try:
        parsed = json.loads(body)
    except ValueError:
        return True, body
    if parsed.get("error_code"):
        return False, f"{parsed['error_code']}: {parsed.get('error', '')[:160]}"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--apply", action="store_true",
                    help="actually cancel; without it this is a dry run")
    ap.add_argument("--older-than-hours", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0,
                    help="cap how many runs to touch (0 = no cap)")
    ap.add_argument("--base-url",
                    default=f"http://127.0.0.1:{os.getenv('PORT', '8080')}",
                    help="the app's own listener; default reads $PORT")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    mgr = _connect()

    print("=== AUTHORITATIVE census BEFORE (event log, row as fallback) ===")
    before, labels = authoritative_census(mgr)
    for status, count in sorted(before.items(), key=lambda kv: -kv[1]):
        print(f"  {status:28} {count}")
    stuck_before = sum(
        c for s, c in before.items() if s.split()[0] not in TERMINAL_VERBS
    )
    print(f"  {'--> non-terminal':28} {stuck_before}")

    # Shown because the gap is itself the finding: the STATUS column is not
    # maintained for cancel/resume/fail, so anything that trusts it is wrong.
    rows_by_column = row_census(mgr)
    column_stuck = sum(rows_by_column.get(s, 0) for s in NON_TERMINAL)
    print(f"  (the STATUS column claims {column_stuck} non-terminal — it is "
          f"not maintained for cancel/resume/fail)")

    rows = candidates(mgr, args.older_than_hours, labels)
    if args.limit:
        rows = rows[:args.limit]

    print()
    print(f"=== {len(rows)} candidates older than {args.older_than_hours}h ===")
    for status, count in sorted(Counter(s for _, s, _ in rows).items()):
        print(f"  {status:12} {count}")
    if rows:
        print(f"  oldest {rows[0][2]:.1f}h ({rows[0][2] / 24:.1f} days)   "
              f"newest {rows[-1][2]:.1f}h")

    if not rows:
        print("\nNothing to do.")
        mgr.close()
        return 0

    if not args.apply:
        print()
        print(f"DRY RUN — nothing changed. Would POST {args.base_url}"
              "/api/v1/runs/<id>/cancel for each. Re-run with --apply.")
        for run_id, status, age_h in rows[:20]:
            print(f"  {run_id}  {status:9} {age_h:8.1f}h")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        mgr.close()
        return 0

    print()
    print(f"=== APPLYING via {args.base_url} ===")
    ok = 0
    failures: Counter = Counter()
    examples: dict[str, str] = {}
    for index, (run_id, status, _age) in enumerate(rows, 1):
        succeeded, detail = cancel(args.base_url, run_id, args.timeout)
        if succeeded:
            ok += 1
        else:
            key = detail.split(":", 1)[0][:40]
            failures[key] += 1
            examples.setdefault(key, f"{run_id} ({status}) -> {detail}")
        if index % 50 == 0:
            print(f"  {index}/{len(rows)}  ok={ok}  failed={sum(failures.values())}")

    print(f"  cancelled: {ok}/{len(rows)}")
    if failures:
        print("  failures by kind:")
        for kind, count in failures.most_common():
            print(f"    {count:5}  {kind}")
            print(f"           e.g. {examples[kind]}")

    print()
    print("=== AUTHORITATIVE census AFTER ===")
    after, _ = authoritative_census(mgr)
    for status in sorted(set(before) | set(after)):
        now, then = after.get(status, 0), before.get(status, 0)
        print(f"  {status:28} {now:6}  ({now - then:+d})")
    stuck_after = sum(
        c for s, c in after.items() if s.split()[0] not in TERMINAL_VERBS
    )
    print(f"  {'--> non-terminal':28} {stuck_after:6}  "
          f"({stuck_after - stuck_before:+d})")

    mgr.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
