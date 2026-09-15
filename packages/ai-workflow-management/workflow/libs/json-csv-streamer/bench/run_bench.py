# -*- coding: utf-8 -*-
"""SA-1905 C1 — RSS load benchmark driver.

Generates JSONL at several sizes, runs OLD (in-RAM dict_to_csv) and NEW
(streaming jsonl_to_csv) each in its own subprocess, and tabulates peak RSS.
OLD is skipped above OLD_MAX_MB (it blows up ~12x). Set WCP_DIR to the
workflow-control-plane-service dir to enable the OLD comparison.

Usage:  WCP_DIR=/path/to/workflow-control-plane-service python run_bench.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHILD = os.path.join(HERE, "bench_convert.py")
BENCH_DIR = os.environ.get("BENCH_DIR", os.path.join(HERE, "_data"))
WCP_DIR = os.environ.get("WCP_DIR", "")
OLD_MAX_MB = int(os.environ.get("OLD_MAX_MB", "320"))

# Row shape below is ~200 B, so rows ≈ bytes / 200.
#
# The four large sizes answer "does it OOM". The three small ones answer a
# question the original set could not reach at all: CHUNKING_THRESHOLD_BYTES is
# 1 MB, and everything at or just above it takes the streaming path — but the
# smallest size ever measured was ~50 MB, fifty times past the decision point.
# A threshold set on memory evidence needs the rate at the threshold to be
# defensible, so the set starts AT it.
SIZES = [
    ("~1MB", 5_000),        # exactly CHUNKING_THRESHOLD_BYTES
    ("~5MB", 25_000),
    ("~16MB", 80_000),      # RESOLVE_MAX_INPUT_BYTES (worker input cap)
    ("~50MB", 250_000),
    ("~150MB", 750_000),
    ("~300MB", 1_500_000),
    ("~500MB", 2_500_000),
]


def _row(i):
    return {
        "id": i, "sku": f"SKU-{i:08d}", "qty": i % 97,
        "price": round((i % 100000) / 7.0, 2),
        "name": f"Item {i} widget assembly unit", "active": (i % 2 == 0),
        "meta": {"warehouse": f"WH-{i % 50}", "region": f"R{i % 9}", "lane": i % 12},
        "tags": ["alpha", "beta", "gamma"],
    }


def gen(path, n):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps(_row(i)))
            f.write("\n")
    return os.path.getsize(path)


def run(mode, inp, out):
    env = dict(os.environ)
    if WCP_DIR:
        env["WCP_DIR"] = WCP_DIR
    cwd = WCP_DIR or None
    r = subprocess.run(
        [sys.executable, CHILD, "--mode", mode, "--input", inp, "--output", out],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("RESULT"):
            return dict(kv.split("=", 1) for kv in ln.split()[1:])
    return {"error": (r.stderr or r.stdout or "no output")[-400:]}


def main():
    os.makedirs(BENCH_DIR, exist_ok=True)
    table = []
    for label, n in SIZES:
        inp = os.path.join(BENCH_DIR, f"in_{n}.jsonl")
        in_mb = gen(inp, n) / 1e6
        print(f"\n=== {label} ({in_mb:.0f} MB, {n:,} rows) ===", flush=True)
        modes = ["old", "new"] if (WCP_DIR and in_mb <= OLD_MAX_MB) else ["new"]
        res = {}
        for mode in modes:
            out = os.path.join(BENCH_DIR, f"out_{n}_{mode}.csv")
            res[mode] = run(mode, inp, out)
            r = res[mode]
            if "error" in r:
                print(f"  {mode:4} ERROR: {r['error']}", flush=True)
            else:
                print(f"  {mode:4} peak_rss={float(r['peak_mb']):8.1f} MB  "
                      f"delta={float(r['delta_mb']):7.1f} MB  secs={float(r['secs']):6.2f}", flush=True)
            try:
                os.remove(out)
            except OSError:
                pass
        table.append((label, res))
        try:
            os.remove(inp)
        except OSError:
            pass

    _summary_rss(table)
    _summary_throughput(table)


def _f(d, key):
    """A float from a RESULT line, or None when that run was skipped/errored."""
    try:
        return float(d[key])
    except (KeyError, TypeError, ValueError):
        return None


def _summary_rss(table):
    print("\n================= SUMMARY: peak RSS (MB) =================")
    print(f"{'input':>8} | {'OLD dict_to_csv':>16} | {'NEW streaming':>14} | {'ratio':>7}")
    print("-" * 58)
    for label, res in table:
        o, nw = res.get("old", {}), res.get("new", {})
        op_v, np_v = _f(o, "peak_mb"), _f(nw, "peak_mb")
        op = f"{op_v:.0f}" if op_v is not None else "(skip)"
        npk = f"{np_v:.0f}" if np_v is not None else "ERR"
        ratio = f"{op_v / np_v:.1f}x" if op_v is not None and np_v else ""
        print(f"{label:>8} | {op:>16} | {npk:>14} | {ratio:>7}")


def _summary_throughput(table):
    """The other half of the question, which peak RSS cannot answer.

    RSS says whether a payload FITS. It says nothing about what the conversion
    costs in wall-clock, and the threshold that routes work to the streaming
    path (``CHUNKING_THRESHOLD_BYTES``, 1 MB) was chosen on the memory evidence
    alone. Two things only a rate column can show:

    * whether the streaming path is LINEAR in input, or whether its two passes
      plus per-cell allocation make the constant grow with size;
    * whether it is actually FASTER than the in-RAM path near the threshold.
      It reads the input twice from disk where ``dict_to_csv`` reads once into
      RAM, so being slower for small payloads is a real possibility, not a
      hypothetical — and a slower default below the threshold would be paid on
      every small output in the system.

    ``secs`` is what the child already measured; nothing new is timed here.
    """
    print("\n============== SUMMARY: throughput (wall-clock) ==============")
    print(f"{'input':>8} | {'rows':>10} | {'OLD s':>7} {'OLD MB/s':>9} | "
          f"{'NEW s':>7} {'NEW MB/s':>9} | {'NEW/OLD':>8}")
    print("-" * 76)
    for label, res in table:
        o, nw = res.get("old", {}), res.get("new", {})
        cells = []
        for d in (o, nw):
            secs, in_mb = _f(d, "secs"), _f(d, "in_mb")
            if secs is None or in_mb is None:
                cells.append(("(skip)", ""))
            else:
                cells.append((f"{secs:.2f}", f"{in_mb / secs:.1f}" if secs else "-"))
        o_s, n_s = _f(o, "secs"), _f(nw, "secs")
        rel = f"{n_s / o_s:.2f}x" if o_s and n_s else ""
        rows = _f(nw, "rows") or _f(o, "rows")
        rows_s = f"{rows:,.0f}" if rows is not None else "-"
        print(f"{label:>8} | {rows_s:>10} | {cells[0][0]:>7} {cells[0][1]:>9} | "
              f"{cells[1][0]:>7} {cells[1][1]:>9} | {rel:>8}")
    print("\nNEW/OLD < 1.00 = streaming is faster.  MB/s is over the INPUT JSONL.")


if __name__ == "__main__":
    main()
