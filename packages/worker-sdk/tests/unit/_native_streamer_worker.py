"""SA-1905 — import the native streamer, or fail loudly when CI expected it.

The worker-sdk twin of the control plane's ``tests/unit/_native_streamer.py``,
and for the same reason: ``pytest.importorskip`` turns a missing wheel into a
skip, and a skip reads as a pass on a green board. The skip is right on a laptop
that has not run ``maturin build``; it is never right in CI.
"""

from __future__ import annotations

import os

import pytest

REQUIRED_ENV = "JSON_CSV_STREAMER_REQUIRED"


def load():
    try:
        import json_csv_streamer  # noqa: PLC0415 - deliberately late/optional

        return json_csv_streamer
    except ImportError as exc:
        if os.environ.get(REQUIRED_ENV) == "1":
            pytest.fail(
                f"{REQUIRED_ENV}=1 but `import json_csv_streamer` failed: {exc}\n"
                "CI builds this wheel, so this is a real regression — a broken "
                "crate or an ABI/version mismatch — not a missing local build.",
                pytrace=False,
            )
        pytest.skip(
            "native json_csv_streamer wheel not installed — build it with "
            "`maturin build` in libs/json-csv-streamer. Set "
            f"{REQUIRED_ENV}=1 to make this a failure instead of a skip.",
            allow_module_level=True,
        )
