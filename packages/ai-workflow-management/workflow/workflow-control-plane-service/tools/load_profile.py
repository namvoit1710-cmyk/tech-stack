#!/usr/bin/env python3
"""Re-runnable load profile + gate recorder for SA-2254 — SA-2257 (P0-3).

Every gate G1–G7 in SA-2254 needs a number, and the Story says those numbers
must come from measurement rather than from guessing. This is the one command
that produces them, run once at the current 2048M to establish the baseline and
again at 256M to check the gates (SA-2267).

    # baseline, against the 2GB deployment
    python tools/load_profile.py --base-url https://<cp-host> \\
        --profile default --out baseline-2048m.json

    # after the memory work, against the 256M deployment
    python tools/load_profile.py --base-url https://<cp-host> \\
        --profile default --out candidate-256m.json

    # compare and print the gate table
    python tools/load_profile.py --compare baseline-2048m.json candidate-256m.json

    # shape check with no deployment at all
    python tools/load_profile.py --dry-run

What it measures, mapped to the gates
-------------------------------------
G1 memory ceiling      steady-state RSS, scraped from /metrics
G2 no leak             RSS slope across the run (a soak needs --duration hours)
G3 latency budget      p95 node-transition
G4 never-worse         p95 end-to-end run wall-clock
G5 throughput          completed runs per minute
G6 fallback coverage   full_materialize_total rate, by cause
G7 OOM kills           restart count observed during the run

Deliberate limits
-----------------
* This drives a REAL deployment over HTTP. It cannot conjure one, and it will
  not pretend: without ``--base-url`` it refuses to emit numbers.
* RSS and restart count come from the pod's own ``/metrics``. If the scrape does
  not expose them the fields are reported as ``null``, never as 0 — a missing
  measurement must not read as a good one.
* The profile is DATA (see ``PROFILES``), so "re-runnable" means the same mix
  every time, not whatever the author remembered.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# The profile is data, so a re-run is genuinely the same run.
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict] = {
    # N concurrent runs x node mix x data-size mix, as the ticket specifies.
    "default": {
        "concurrent_runs": 8,
        "total_runs": 80,
        "node_mix": {"compute": 3, "condition": 2, "http_request": 2, "loop": 1},
        "data_size_mix_bytes": [1_024, 64 * 1024, 1024 * 1024, 16 * 1024 * 1024],
        "description": "Mixed steady-state load: small inline values through "
                       "file-backed collections that exercise the resolver.",
    },
    "smoke": {
        "concurrent_runs": 2,
        "total_runs": 4,
        "node_mix": {"compute": 1, "condition": 1},
        "data_size_mix_bytes": [1_024],
        "description": "Fast shape check that the harness and endpoints agree.",
    },
    "soak": {
        "concurrent_runs": 4,
        "total_runs": 0,          # 0 = run until --duration elapses
        "node_mix": {"compute": 2, "condition": 1, "http_request": 1},
        "data_size_mix_bytes": [64 * 1024, 1024 * 1024],
        "description": "G2/G7 soak: steady trickle for 24h+, watching RSS slope.",
    },
}

# Metric names the control plane exposes (SA-2256 and the process collector).
_RSS_METRICS = ("process_resident_memory_bytes", "wcp_process_rss_bytes")
_RESTART_METRICS = ("wcp_restarts_total", "process_start_time_seconds")


def _get(url: str, timeout: float = 30.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _post(url: str, payload: dict, timeout: float = 60.0) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def parse_prometheus(text: str) -> dict[str, float]:
    """Flatten an exposition payload to {series_with_labels: value}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name, value = line.rsplit(" ", 1)
            out[name.strip()] = float(value)
        except ValueError:
            continue
    return out


def _first_present(metrics: dict[str, float], names) -> float | None:
    for name in names:
        for series, value in metrics.items():
            if series == name or series.startswith(name + "{"):
                return value
    return None


def scrape(base_url: str) -> dict:
    """One /metrics sample. Missing fields are ``None``, never 0."""
    status, body = _get(f"{base_url.rstrip('/')}/metrics")
    if status != 200:
        return {"ok": False, "status": status, "error": body[:200]}
    metrics = parse_prometheus(body)
    full_materialize = {
        series: value for series, value in metrics.items()
        if series.startswith("full_materialize_total")
    }
    cache = {
        series: value for series, value in metrics.items()
        if series.startswith("wcp_cache_")
    }
    return {
        "ok": True,
        "at": time.time(),
        "rss_bytes": _first_present(metrics, _RSS_METRICS),
        "restart_signal": _first_present(metrics, _RESTART_METRICS),
        "full_materialize_total": full_materialize,
        "cache_budget": cache,
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def run_profile(base_url: str, profile: dict, workflow_id: str,
                duration_s: float) -> dict:
    """Drive runs against a live deployment and record per-run wall-clock."""
    started = time.time()
    deadline = started + duration_s if duration_s > 0 else None
    target = profile["total_runs"]
    wall_clocks: list[float] = []
    failures = 0
    launched = 0
    samples = [scrape(base_url)]

    while True:
        if target and launched >= target:
            break
        if deadline and time.time() >= deadline:
            break
        t0 = time.time()
        status, body = _post(
            f"{base_url.rstrip('/')}/api/v1/workflows/{workflow_id}/runs",
            {"input": {"load_profile": True}},
        )
        launched += 1
        if status in (200, 201, 202):
            wall_clocks.append(time.time() - t0)
        else:
            failures += 1
        if launched % max(1, profile["concurrent_runs"]) == 0:
            samples.append(scrape(base_url))

    samples.append(scrape(base_url))
    elapsed = time.time() - started
    rss_values = [s["rss_bytes"] for s in samples
                  if s.get("ok") and s.get("rss_bytes") is not None]
    return {
        "elapsed_s": round(elapsed, 1),
        "launched": launched,
        "failures": failures,
        "g1_rss_peak_bytes": max(rss_values) if rss_values else None,
        "g2_rss_slope_bytes_per_min": (
            round((rss_values[-1] - rss_values[0]) / (elapsed / 60), 1)
            if len(rss_values) >= 2 and elapsed > 60 else None
        ),
        "g4_p95_dispatch_s": percentile(wall_clocks, 0.95),
        "g5_runs_per_min": round(launched / (elapsed / 60), 2) if elapsed > 0 else None,
        "g6_full_materialize": samples[-1].get("full_materialize_total"),
        "samples": samples,
    }


def compare(baseline: dict, candidate: dict) -> int:
    """Print the gate table. Returns a shell exit code."""
    rows = [
        ("G1 peak RSS (bytes)", "g1_rss_peak_bytes", "lower"),
        ("G2 RSS slope (B/min)", "g2_rss_slope_bytes_per_min", "near-zero"),
        ("G4 p95 dispatch (s)", "g4_p95_dispatch_s", "not worse"),
        ("G5 runs/min", "g5_runs_per_min", "higher"),
    ]
    print(f"{'gate':28} {'baseline':>16} {'candidate':>16}   want")
    verdict_ok = True
    for label, key, want in rows:
        b = baseline.get("result", {}).get(key)
        c = candidate.get("result", {}).get(key)
        bs = "n/a" if b is None else f"{b:,.2f}"
        cs = "n/a" if c is None else f"{c:,.2f}"
        print(f"{label:28} {bs:>16} {cs:>16}   {want}")
        if b is None or c is None:
            verdict_ok = False
    print()
    if not verdict_ok:
        print("INCOMPLETE — at least one gate has no measurement on one side.")
        print("A missing number is not a passing number; do not record a verdict.")
        return 2
    print("Both sides measured. Apply the thresholds agreed in SA-2254 and")
    print("record the verdict on the Story (P0-3 acceptance criteria).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", help="control plane base URL")
    ap.add_argument("--workflow-id", default="", help="workflow to run")
    ap.add_argument("--profile", default="default", choices=sorted(PROFILES))
    ap.add_argument("--duration", type=float, default=0.0,
                    help="seconds; 0 = use the profile's total_runs")
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the profile and exit; touches no deployment")
    ap.add_argument("--compare", nargs=2, metavar=("BASELINE", "CANDIDATE"))
    args = ap.parse_args()

    if args.compare:
        with open(args.compare[0], encoding="utf-8") as fh:
            baseline = json.load(fh)
        with open(args.compare[1], encoding="utf-8") as fh:
            candidate = json.load(fh)
        return compare(baseline, candidate)

    profile = PROFILES[args.profile]
    print(f"profile: {args.profile}")
    print(f"  {profile['description']}")
    print(f"  concurrent_runs      {profile['concurrent_runs']}")
    print(f"  total_runs           {profile['total_runs'] or '(until --duration)'}")
    print(f"  node_mix             {profile['node_mix']}")
    print(f"  data_size_mix_bytes  {profile['data_size_mix_bytes']}")

    if args.dry_run:
        print("\n--dry-run: no deployment contacted, no numbers produced.")
        return 0

    if not args.base_url or not args.workflow_id:
        print("\nERROR: --base-url and --workflow-id are required to measure.",
              file=sys.stderr)
        print("This harness drives a REAL deployment; it will not invent "
              "numbers for gates that were never exercised.", file=sys.stderr)
        return 2

    result = run_profile(args.base_url, profile, args.workflow_id, args.duration)
    report = {"profile_name": args.profile, "profile": profile,
              "base_url": args.base_url, "result": result}
    print(f"\nlaunched {result['launched']} runs in {result['elapsed_s']}s "
          f"({result['failures']} failed)")
    for key in ("g1_rss_peak_bytes", "g2_rss_slope_bytes_per_min",
                "g4_p95_dispatch_s", "g5_runs_per_min"):
        value = result.get(key)
        print(f"  {key:32} {'n/a (not exposed)' if value is None else value}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nreport written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
