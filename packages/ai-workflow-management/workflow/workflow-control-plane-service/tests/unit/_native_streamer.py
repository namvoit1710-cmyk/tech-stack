"""SA-1905 F1 — import the native streamer, or fail loudly when CI expected it.

``pytest.importorskip("json_csv_streamer")`` is how these suites used to start,
and it is what kept 397 lines of shipped Rust uncovered: ``ct-python.yml`` never
built the wheel, so every test in the file skipped, and a skip reads as a pass on
a green board. Nothing was broken — nothing was being checked.

The skip is still correct on a laptop that has not run ``maturin build``. It is
never correct in CI. ``ct-python.yml`` now builds the wheel and exports
``JSON_CSV_STREAMER_REQUIRED=1``; with that set an ImportError is a failure.
"""

from __future__ import annotations

import os

import pytest

REQUIRED_ENV = "JSON_CSV_STREAMER_REQUIRED"


def load():
    """Return the native module, skipping (dev) or failing (CI) if it is absent."""
    try:
        import json_csv_streamer  # noqa: PLC0415 - deliberately late/optional

        return json_csv_streamer
    except ImportError as exc:
        if os.environ.get(REQUIRED_ENV) == "1":
            pytest.fail(
                f"{REQUIRED_ENV}=1 but `import json_csv_streamer` failed: {exc}\n"
                "CI builds this wheel (ct-python.yml, 'Build the native "
                "json-csv-streamer wheel'), so this is a real regression — a "
                "broken crate or an ABI/version mismatch — not a missing local "
                "build. Skipping here would hide exactly the bug the step exists "
                "to surface.",
                pytrace=False,
            )
        pytest.skip(
            "native json_csv_streamer wheel not installed — build it with "
            "`maturin build` in libs/json-csv-streamer, or run `cargo test` "
            f"there for the Rust-side suite. Set {REQUIRED_ENV}=1 to make this "
            "a failure instead of a skip.",
            allow_module_level=True,
        )
