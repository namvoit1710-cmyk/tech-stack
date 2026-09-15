# SA-1905 C1 — RSS and throughput

Concrete evidence that the streaming JSON→CSV producer keeps a **bounded** memory
footprint regardless of payload size, versus the control plane's in-RAM
`dict_to_csv` — and, since 2026-08-06, what each path **costs in wall-clock**.

## Method

Both paths convert the **same on-disk JSONL** file (fair conversion-cost
comparison). Each conversion runs in its **own subprocess** that samples its own
whole-process RSS every ~3 ms and reports the peak, and times the conversion with
`time.monotonic()`. Baseline RSS is captured **after** importing the conversion
module, so `delta` is the conversion working-set only.

**Caveat on the RSS column, which this file used to state too confidently:** that
in-child sampler is a Python thread, so it only samples the OLD path. Once
`py.allow_threads` landed it samples the NEW path too, but every RSS number in the
table below predates that and was taken while the sampler was GIL-starved — see
the section at the end, where the NEW figure is re-verified from outside the
process at **14.0 MB**. The conclusion is unchanged and the evidence is now real.

- Row shape: `{id, sku, qty, price, name, active, meta:{warehouse,region,lane}, tags:[...]}` (~200 B/row).
- `old` = `CsvConverter.dict_to_csv(list(all rows))` — the current CP path.
- `new` = `json_csv_streamer.jsonl_to_csv(in, out)` — streaming two-pass.
- Host: 33.8 GB RAM, 10C/12T, Python 3.13, rustc 1.94 (release wheel). `old` skipped
  above 320 MB input for safety (it blows up ~12×).

The three smallest sizes were added on 2026-08-06. `CHUNKING_THRESHOLD_BYTES` is
**1 MB** and everything at or above it takes the streaming path, but the smallest
size ever measured was ~50 MB — fifty times past the decision point. A threshold
needs evidence *at* the threshold.

Reproduce:
```
WCP_DIR=/path/to/workflow-control-plane-service python run_bench.py
```

## Result — peak RSS

| input JSONL          | OLD `dict_to_csv` peak RSS | NEW streaming peak RSS | ratio |
|----------------------|---------------------------|------------------------|-------|
| 1 MB (5k rows)       | 37 MB                     | **21 MB**              | 1.8×  |
| 6 MB (25k rows)      | 88 MB                     | **21 MB**              | 4.3×  |
| 18 MB (80k rows)     | 229 MB                    | **21 MB**              | 11.1× |
| 56 MB (250k rows)    | **696 MB**                | **21 MB** (Δ 0.5 MB)   | 33.8× |
| 167 MB (750k rows)   | **2043 MB**               | **20 MB** (Δ 0.5 MB)   | 99.7× |
| 336 MB (1.5M rows)   | (skipped, ≈4 GB projected)| **21 MB** (Δ 0.5 MB)   | —     |
| 561 MB (2.5M rows)   | (skipped, ≈6 GB projected)| **21 MB** (Δ 0.6 MB)   | —     |

## Result — throughput

| input JSONL | rows  | OLD s | OLD MB/s | NEW s | NEW MB/s | NEW/OLD |
|-------------|-------|-------|----------|-------|----------|---------|
| 1 MB        | 5k    | 0.10  | 11.0     | 0.05  | 22.0     | **0.50×** |
| 6 MB        | 25k   | 0.35  | 15.7     | 0.24  | 22.9     | 0.69×   |
| 18 MB       | 80k   | 1.07  | 16.5     | 0.70  | 25.3     | 0.65×   |
| 56 MB       | 250k  | 3.45  | 16.1     | 2.24  | 24.8     | 0.65×   |
| 167 MB      | 750k  | 10.61 | 15.8     | 7.07  | 23.7     | 0.67×   |
| 336 MB      | 1.5M  | (skip)| —        | 14.44 | 23.3     | —       |
| 561 MB      | 2.5M  | (skip)| —        | 33.99 | **16.5** | —       |

`NEW/OLD < 1.00` = streaming is faster. MB/s is over the **input** JSONL.

## Conclusions

1. **OLD path RSS scales ~12× the input** (holds the full list + every flattened
   row + the `all_keys` union + the output buffer simultaneously). A 500 MB
   payload → ~6 GB → OOMs any worker/CP.
2. **NEW streaming path is O(1) in data size** — a flat ~21 MB peak (0.4–0.6 MB
   conversion delta over the ~20 MB Python baseline) from 1 MB to 561 MB input.
3. **A 128 MB worker handles a 500 MB payload with ~6× headroom.** The design's
   "may need to bump the large-output worker to 256 MB" concern is **resolved**:
   128 MB is ample for the streaming path.
4. **Concurrency bound (C1 part b):** disk *capacity*, not RAM, is the shared
   limit — each concurrent large task uses ~1–1.5 GB of the 5 GB `/tmp` (input
   JSONL + output CSV). Bound concurrent large-output tasks with the existing
   `WORKER_MAX_CONCURRENT` (SA-1530) so N × ~1.5 GB stays under 5 GB — i.e. cap
   ≈ 3 concurrent 500 MB-class tasks per instance (tune to the disk quota).
   Note this is about *fitting*, not speed: at 17–25 MB/s the converter runs at
   well under 1% of NVMe read bandwidth, so it is CPU-bound, not I/O-bound.
5. **Streaming is also FASTER at every measured size, including exactly at the
   1 MB threshold** (0.50–0.69× the old path's time). This was not obvious and is
   worth stating: the streaming path reads the input **twice** where `dict_to_csv`
   reads once, so it could plausibly have lost near the threshold. It does not —
   the old path's cost is dominated by building Python objects (a dict per row
   from `json.loads`, a second dict from `_flatten`, then `DictWriter`), which
   swamps a second sequential read that is served from page cache anyway.
   **`CHUNKING_THRESHOLD_BYTES = 1 MB` is therefore defensible on both axes**, and
   there is no speed argument for raising it.
6. **The apparent knee at 561 MB was measurement noise — page cache was not the
   cause.** An earlier revision of this file proposed that the input is evicted
   between pass 1 and pass 2 at the top size. Repeating each size three times
   refutes it: 450 MB and 561 MB differ by only **3.6%** (pass 1) and **7%**
   (pass 2), and the same-size repeats themselves drift downward
   (48.6 → 47.4 → 45.9 MB/s at 450 MB), which is host drift, not a function of
   input size. The single-run comparison had caught 450 MB at its fastest and
   561 MB at its slowest. Two further arguments against the hypothesis were
   already in the data: three back-to-back runs at 561 MB showed **no** warming
   trend, and the whole working set (561 MB in + 305 MB out) is 2.6% of this
   host's RAM.
7. **The real structural cost is the second pass.** Decomposing `jsonl_to_csv`
   into its exported halves, each pass holds a flat per-byte rate:

   | input  | pass 1 (`schema_sketch`, read only) | pass 2 (`write_csv`, read + write) |
   |--------|-------------------------------------|------------------------------------|
   | 167 MB | 3.33 s — 50.2 MB/s                  | 3.93 s — 42.6 MB/s                 |
   | 336 MB | 6.65 s — 50.5 MB/s                  | 7.87 s — 42.7 MB/s                 |
   | 449 MB | 9.49 s — 47.3 MB/s (3-run mean)     | 12.24 s — 36.7 MB/s (3-run mean)   |
   | 561 MB | 12.30 s — 45.6 MB/s (3-run mean)    | 16.43 s — 34.1 MB/s (3-run mean)   |

   Each pass runs at 34–50 MB/s; end-to-end is ~23 MB/s because the file is read
   **twice**. **Pass 1 is 43–46% of the total and produces only a list of column
   names.** It exists because CSV requires the header before any row and the input
   is ragged. That, not cache behaviour, is where a single-pass design would pay —
   and it is the honest answer to "single-pass or allocation reduction".

## Why the 561 MB number moved: 22 s → 33 s

The 2026-07-11 revision of this file recorded **~22 s** for 561 MB. The same host
now measures **32.7 s** (32.34 / 33.03 / 32.71 over three runs — stable, not noise).

That is not a regression to fix. It is the price of a correctness fix, measured
here for the first time. `arbitrary_precision` was added to `serde_json` on
**2026-07-15**, four days *after* this benchmark was recorded, and the benchmark
was never re-run. Building the current source with that one feature removed:

| 561 MB build           | run 1 | run 2 | run 3 | mean  | `10**25` round-trip |
|------------------------|-------|-------|-------|-------|---------------------|
| **with** `arbitrary_precision` (shipped) | 32.34 | 33.03 | 32.71 | **32.7 s** | `10000000000000000000000000` — exact |
| without                | 26.04 | 24.37 | 23.88 | **24.8 s** | `1e+25` — **lossy** |

**~32% of throughput buys exact large-integer round-trip.** For a system whose
payloads are full of long identifiers that is the right trade, and the Cargo.toml
comment already argues it — but the cost was previously unrecorded, and 24.8 s is
close enough to the original 22 s to account for most of the drift (the remainder
being the `value_mode` / `item_kind` work in the same window).

**Re-run this benchmark whenever the crate's parse path changes.** A silent 32%
is exactly the size of change that never gets noticed at PR time.

## The measurement flaw this benchmark had, and what it uncovered

**The in-child RSS sampler cannot run during the NEW path.** It sleeps (releasing
the GIL) and must *reacquire* the GIL to run its loop — but a PyO3 function holds
the GIL for its whole duration, so for `jsonl_to_csv` the sampler was frozen
exactly when it was supposed to be sampling. The header's claim that it "samples
its own whole-process RSS every ~3 ms" was true only of the OLD path, which is
pure Python and yields the GIL every few bytecodes.

The conclusion survives, verified properly by sampling the child **from the
parent process**, which no GIL can block: **14.0 MB peak over 8,515 real samples**
during a 561 MB conversion — better than the ~21 MB recorded here, which included
the child's own `psutil`-importing Python baseline.

Chasing that led to the more consequential finding: **the conversion held the GIL
for its entire duration.** A 100 ms ticker thread during a 561 MB conversion:

```
conversion            : 39.6 s
expected ~396 ticks   : got 2
longest single stall  : 39.7 s
```

For 40 seconds nothing else in the process ran — not the worker heartbeat
(`HEARTBEAT_INTERVAL_SECONDS` is 30, so at least one beat is missed), not lease
renewal (an asyncio task, so it cannot fire whatever `lease_ttl_seconds` the
executor set), not any other task the same worker had in flight. A lapsed lease
is not a slow task; it is a task the executor hands to another worker while this
one is still running it.

Fixed by wrapping every exported function in `py.allow_threads`. Verified
back-to-back on the same 561 MB input:

| | output sha256 | conversion | ticks (of ~expected) | longest stall |
|---|---|---|---|---|
| before | `a7e68203…` | 24.3 s | **2** of ~243 | **24.4 s** |
| after  | `a7e68203…` | 25.9 s | **259** of ~259 | **0.1 s** |

Byte-identical output, throughput unchanged (1.02×, within noise), `10**25` still
exact. The fix buys no speed at all — it only stops that time being taken from
everything else in the process. Locked in by
`tests/unit/test_json_csv_streamer.py::TestTheGilIsReleasedForTheWholeConversion`,
which asserts a *ratio* of observed to expected ticks and a bound on the longest
stall; an earlier version asserting merely `ticks > 0` passed against the un-fixed
wheel, because a starved thread still records one gap the moment it reacquires.

**If those tests fail locally, rebuild the wheel** — a stale
`site-packages/json_csv_streamer` from before this change will fail them
correctly.
