# -*- coding: utf-8 -*-
"""SA-1905 C1 — child: convert one on-disk JSONL to CSV via a chosen path,
sampling this process's own peak whole-process RSS (native Rust footprint
included). Baseline captured AFTER the import so delta = conversion working-set.

  new  -> json_csv_streamer.jsonl_to_csv (streaming two-pass).
  old  -> control-plane in-RAM CsvConverter.dict_to_csv. Requires the WCP
          service dir on sys.path — set WCP_DIR (or run with cwd=WCP).
"""
import argparse
import json
import os
import sys
import threading
import time

# Old-path comparison imports the control-plane CsvConverter; make it findable.
_wcp = os.environ.get("WCP_DIR") or os.getcwd()
if _wcp and _wcp not in sys.path:
    sys.path.insert(0, _wcp)

import psutil


def _sampler(proc, stop, out, interval=0.003):
    peak = 0
    while not stop.is_set():
        try:
            rss = proc.memory_info().rss
            peak = max(peak, rss)
        except Exception:
            pass
        time.sleep(interval)
    try:
        peak = max(peak, proc.memory_info().rss)
    except Exception:
        pass
    out["peak"] = peak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["old", "new"])
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    proc = psutil.Process()
    if args.mode == "old":
        from app.layer2_application.data.csv_converter import CsvConverter
    else:
        import json_csv_streamer as jcs

    baseline = proc.memory_info().rss
    stop, out = threading.Event(), {}
    t = threading.Thread(target=_sampler, args=(proc, stop, out), daemon=True)
    t.start()

    t0 = time.monotonic()
    if args.mode == "old":
        with open(args.input, encoding="utf-8") as f:
            data = [json.loads(line) for line in f if line.strip()]
        csv_bytes, _fn, rows = CsvConverter.dict_to_csv(data)
        with open(args.output, "wb") as f:
            f.write(csv_bytes)
    else:
        _cols, _types, rows, _item_kind = jcs.jsonl_to_csv(args.input, args.output)
    secs = time.monotonic() - t0

    stop.set()
    t.join()
    peak = out.get("peak", proc.memory_info().rss)
    print(
        f"RESULT mode={args.mode} rows={rows} "
        f"in_mb={os.path.getsize(args.input)/1e6:.1f} out_mb={os.path.getsize(args.output)/1e6:.1f} "
        f"baseline_mb={baseline/1e6:.1f} peak_mb={peak/1e6:.1f} "
        f"delta_mb={(peak-baseline)/1e6:.1f} secs={secs:.2f}"
    )


if __name__ == "__main__":
    main()
