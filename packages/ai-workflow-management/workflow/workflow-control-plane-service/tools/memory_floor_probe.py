#!/usr/bin/env python3
"""Measure the control plane's memory FLOOR and each transport's share — SA-2255.

Why this exists
---------------
P0-1 is BLOCKING for SA-2254 and asks one question: how much of a 256MB pod is
gone before any work happens? The control plane loads an unusually heavy native
set — grpcio, confluent-kafka (librdkafka), python-qpid-proton, hdbcli,
cryptography, pydantic-core, polars, plus a Rust wheel. If that floor exceeds
~140MB then 256MB is not a cache problem and the whole programme changes shape.

Run it
------
    # inside the pod, which is the only place the answer is authoritative
    cf ssh ai-workflow-control-plane -c "cd /home/vcap/app && python tools/memory_floor_probe.py"

    # locally, for a fast indicative reading
    python tools/memory_floor_probe.py --json floor.json

What it reports
---------------
1. Bare interpreter RSS.
2. Per-dependency import cost, measured one import at a time in order.
3. The app's own import tree.
4. Per-transport cost, each measured in a FRESH subprocess with that transport's
   env toggle on and the others off — measuring them in one process would charge
   whichever imported first for everything shared.
5. A GO / ESCALATE verdict against the threshold.

Honest limits, printed with the result
--------------------------------------
* Import-time RSS is a FLOOR, not the idle steady state: a booted app also holds
  connection pools, consumer threads and broker client buffers. Treat the number
  as "not less than this".
* RSS accounting differs between Linux cgroups and a developer laptop. A local
  reading is indicative; the pod reading is the answer.
* A dependency that is absent locally is reported as ``n/a`` rather than 0 —
  missing must never read as free.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys

# Threshold from SA-2255: above this, 256MB is not a cache problem.
ESCALATE_THRESHOLD_MB = 140.0

# Ordered so the heavy natives are attributed before the frameworks that may
# pull them in transitively.
DEPENDENCIES = [
    "grpc",
    "confluent_kafka",
    "proton",
    "hdbcli",
    "cryptography",
    "pydantic_core",
    "polars",
    "fastapi",
    "httpx",
]

# What each transport COSTS to have available, measured as the import cost of
# the libraries it needs, in a fresh interpreter.
#
# Measured this way on purpose. An earlier version toggled env vars
# (GRPC_SERVER_ENABLED etc.) and measured the child's RSS — which produced four
# near-identical numbers, because a flag does not change what gets imported and
# the transports are only constructed when the app actually boots. Those numbers
# looked like measurements and measured nothing. Import cost is the part that is
# both real and attributable from outside a running pod; the rest (threads,
# pools, broker buffers) needs the in-pod idle reading.
TRANSPORTS = {
    "grpc_server": ["grpc"],
    "kafka": ["confluent_kafka"],
    "event_mesh_amqp": ["proton"],
    "socketio": ["socketio"],
    "http_server": ["fastapi", "uvicorn"],
}


def _rss_mb() -> float:
    """Resident set size in MiB, without requiring psutil."""
    try:
        import psutil  # noqa: PLC0415 — optional, preferred when present

        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except Exception:
        pass
    # Linux fallback: statm page counts. This is the path that matters in-pod.
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024
    except Exception:
        return -1.0


def measure_dependencies() -> dict:
    baseline = _rss_mb()
    previous = baseline
    rows = []
    for name in DEPENDENCIES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            rows.append({"module": name, "delta_mb": None,
                         "note": f"not importable: {type(exc).__name__}"})
            continue
        current = _rss_mb()
        rows.append({
            "module": name,
            "delta_mb": round(current - previous, 1),
            "cumulative_mb": round(current - baseline, 1),
        })
        previous = current
    return {
        "baseline_mb": round(baseline, 1),
        "after_mb": round(previous, 1),
        "dependencies": rows,
    }


def measure_app_import() -> dict:
    """Cost of importing the application tree itself (no boot, no I/O)."""
    # Running as ``python tools/memory_floor_probe.py`` puts ``tools/`` on
    # sys.path[0], not the service root, so ``bootstrap`` is invisible. Add the
    # service root explicitly rather than depending on how it was invoked.
    service_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if service_root not in sys.path:
        sys.path.insert(0, service_root)
    before = _rss_mb()
    try:
        importlib.import_module("bootstrap")
        note = ""
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"delta_mb": None, "note": f"import failed: {exc}"}
    after = _rss_mb()
    return {"delta_mb": round(after - before, 1),
            "total_mb": round(after, 1), "note": note}


_CHILD_TEMPLATE = """
import importlib, json, os, sys
def rss():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except Exception:
        pass
    try:
        with open('/proc/self/statm') as fh:
            pages = int(fh.read().split()[1])
        return pages * os.sysconf('SC_PAGE_SIZE') / 1024 / 1024
    except Exception:
        return -1.0
before = rss()
missing = []
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(name + ':' + type(exc).__name__)
print(json.dumps({{'before': before, 'after': rss(), 'missing': missing}}))
"""


def measure_transports() -> dict:
    """One FRESH interpreter per transport.

    Measuring them in a single process would charge whichever transport
    imported first for every shared dependency underneath it.
    """
    results = {}
    for name, modules in TRANSPORTS.items():
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            out = subprocess.run(
                [sys.executable, "-c", _CHILD_TEMPLATE.format(modules=modules)],
                env=env, capture_output=True, text=True, timeout=300,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            payload = json.loads(out.stdout.strip().splitlines()[-1])
            missing = payload.get("missing") or []
            results[name] = {
                "modules": modules,
                "cost_mb": None if missing else round(
                    payload["after"] - payload["before"], 1),
                "note": ("absent: " + ", ".join(missing)) if missing else "",
            }
        except Exception as exc:
            results[name] = {"modules": modules, "cost_mb": None,
                             "note": f"{type(exc).__name__}: {exc}"}
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="write the full report to this path")
    parser.add_argument("--skip-transports", action="store_true",
                        help="skip the per-transport subprocess matrix")
    args = parser.parse_args()

    report: dict = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "threshold_mb": ESCALATE_THRESHOLD_MB,
    }

    deps = measure_dependencies()
    report["dependency_imports"] = deps
    print(f"bare interpreter                {deps['baseline_mb']:8.1f} MB")
    for row in deps["dependencies"]:
        if row["delta_mb"] is None:
            print(f"  {row['module']:28} {'n/a':>8}   {row['note']}")
        else:
            print(f"  {row['module']:28} {row['delta_mb']:+8.1f} MB"
                  f"   (cumulative {row['cumulative_mb']:.1f})")
    print(f"after all dependencies          {deps['after_mb']:8.1f} MB")

    app = measure_app_import()
    report["app_import"] = app
    if app.get("delta_mb") is None:
        print(f"app import tree                     n/a   {app.get('note')}")
        floor = deps["after_mb"]
    else:
        print(f"app import tree                 {app['delta_mb']:+8.1f} MB")
        print(f"FLOOR (imports, no traffic)     {app['total_mb']:8.1f} MB")
        floor = app["total_mb"]

    if not args.skip_transports:
        transports = measure_transports()
        report["transports"] = transports
        print("\nper-transport import cost (fresh interpreter each):")
        for name, row in transports.items():
            value = row.get("cost_mb")
            shown = (f"{value:+8.1f} MB" if value is not None
                     else f"{'n/a':>8}   {row.get('note', '')}")
            print(f"  {name:28} {shown}")

    report["floor_mb"] = floor
    verdict = "GO" if floor <= ESCALATE_THRESHOLD_MB else "ESCALATE"
    report["verdict"] = verdict
    print(f"\nVERDICT vs {ESCALATE_THRESHOLD_MB:.0f}MB threshold: {verdict}")
    if verdict == "ESCALATE":
        print("  The floor alone exceeds the threshold: 256MB is NOT a cache")
        print("  problem. Transport consolidation must be costed before any")
        print("  further cache work (SA-2255 acceptance criteria).")

    print("\nLIMITS — read before quoting this number:")
    print("  * Import-time RSS is a FLOOR, not idle steady state. A booted app")
    print("    also holds connection pools, consumer threads and broker buffers.")
    print("  * Only the in-pod reading is authoritative; cgroup accounting and a")
    print("    developer machine do not agree.")
    print("  * 'n/a' means the dependency was ABSENT, not free.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nreport written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
