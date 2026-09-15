"""The input cap must be sized for the pod it actually runs on.

This exists because it was not. On 2026-08-18 the deployed pod was **128 MB**
while the budget's own comment sized itself for *"an 86 MB-free 256 MB pod"* — a
guard reasoning about twice the memory it had. The symptom was run
`b04920b1-...`: a 10 000-row input (23.5 MB fetched) refused at the 16 MiB cap.
Refusing was correct; what was wrong is that the two numbers had never been
reconciled, so nobody could tell whether the cap was conservative or optimistic.

The arithmetic, so a future change cannot move one number without the other:

    peak_resident  ~=  cap_bytes * PEAK_MULTIPLIER + BASELINE
    peak_resident  <=  pod_bytes * SAFE_FRACTION

``PEAK_MULTIPLIER`` is measured, not guessed. For the shape that actually flows
through this worker (wide rows, 61 short string columns) the wire->parsed ratio
was measured at **1.3x** on 50 live rows — the wire form is inflated by repeating
every column name on every row, while memory shares the keys. On top of the
parsed rows the worker holds the re-nested object and the serialized request
body, which is where the rest of the 3.5x comes from.

(The SDK comment quotes 3-40x. That is the ratio for NARROW rows, where
per-object overhead dominates a tiny payload — a different shape, not a
different opinion.)
"""

from __future__ import annotations

import os

import pytest

yaml = pytest.importorskip("yaml")

# Measured peak = parsed rows (1.3x) + re-nested object (~1.3x) + serialized
# request body (~0.9x). Raise this if the worker starts holding another copy.
PEAK_MULTIPLIER = 3.5

# Interpreter + framework + httpx at rest. The live pod idles at ~42 MB.
BASELINE_BYTES = 40 * 1024 * 1024

# Leave room for GC lag, fragmentation, and a second concurrent task.
SAFE_FRACTION = 0.6

_MANIFEST = os.path.join(
    os.path.dirname(__file__), "..", "..", "deployment", "deployment.yml",
)


def _manifest() -> dict:
    with open(os.path.abspath(_MANIFEST), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _pod_bytes(tier: str) -> int:
    raw = str(_manifest()["memory"][tier]).strip().upper()
    if raw.endswith("G"):
        return int(float(raw[:-1]) * 1024 * 1024 * 1024)
    if raw.endswith("M"):
        return int(float(raw[:-1]) * 1024 * 1024)
    raise AssertionError(f"unhandled memory unit: {raw!r}")


def _env(key: str, tier: str):
    entry = (_manifest().get("env") or {}).get(key)
    return None if entry is None else entry[tier]


# tenant-1 (the `solace` env) reads the `dev` tier — deploy.yml resolves
# "main env reads the 'qas' config tier; dev/solace read 'dev'". So the dev row
# is not just dev: it is also what production tenant-1 runs.
TIERS = ["dev", "qas"]


@pytest.mark.parametrize("tier", TIERS)
def test_the_input_cap_is_declared(tier):
    """An undeclared cap silently inherits the SDK default, which is sized for a
    different pod than this one."""
    assert _env("RESOLVE_MAX_INPUT_BYTES", tier) is not None, (
        "declare RESOLVE_MAX_INPUT_BYTES next to the memory it depends on"
    )


@pytest.mark.parametrize("tier", TIERS)
def test_the_input_cap_fits_the_pod(tier):
    cap = int(_env("RESOLVE_MAX_INPUT_BYTES", tier))
    pod = _pod_bytes(tier)
    peak = cap * PEAK_MULTIPLIER + BASELINE_BYTES
    budget = pod * SAFE_FRACTION

    assert peak <= budget, (
        f"[{tier}] cap {cap / 1048576:.0f} MiB implies a ~{peak / 1048576:.0f} MB peak, "
        f"over {SAFE_FRACTION:.0%} of a {pod / 1048576:.0f} MB pod "
        f"({budget / 1048576:.0f} MB). Raise the pod, or lower the cap — "
        f"never one without the other."
    )


@pytest.mark.parametrize("tier", TIERS)
def test_the_node_timeout_ceiling_stays_within_what_the_executor_honours(tier):
    """A ceiling above the executor's clamp is a ceiling that does nothing.

    The executor derives its pending-callback deadline from the node's
    ``timeout_seconds`` + ``TASK_CALLBACK_GRACE_SECONDS`` (30) and clamps it to
    ``TASK_TIMEOUT_MAX_SECONDS`` (3600). Past that the worker keeps waiting while
    the executor has already given up, which surfaces as
    "did not callback in time" and looks like a worker fault.
    """
    ceiling = _env("HTTP_MAX_TIMEOUT_SECONDS", tier)
    if ceiling is None:
        pytest.skip("ceiling not overridden; the code default (300) applies")
    assert int(ceiling) <= 3600 - 30, (
        f"[{tier}] HTTP_MAX_TIMEOUT_SECONDS={ceiling} exceeds what the executor "
        f"will honour (TASK_TIMEOUT_MAX_SECONDS 3600 - grace 30). Raise "
        f"TASK_TIMEOUT_MAX_SECONDS on the executor first, or lower this."
    )


def test_a_worked_example_so_the_numbers_are_checkable():
    """The payload that prompted this: 10 000 rows at ~2 349 B/row on the wire."""
    rows, per_row = 10_000, 2349
    wire = rows * per_row
    cap = int(_env("RESOLVE_MAX_INPUT_BYTES", "dev"))

    assert wire < cap, (
        f"10 000 rows is {wire / 1048576:.1f} MB on the wire and the cap is "
        f"{cap / 1048576:.0f} MiB — the payload this was raised for still would not fit"
    )
