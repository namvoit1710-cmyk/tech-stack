"""Phase 3 — stop storing every TASK output twice, without breaking 15 workflows.

## The duplicate

``complete_task_use_case`` builds a node's output as::

    task_output["metadata"] = req.output_metadata     # the whole worker output
    task_output.update(req.output_metadata)           # ...and again, flattened

Measured on a real live value (2026-08-19): removing the ``metadata`` key takes
it from **886 752 -> 443 371 bytes, exactly 50 %**. Every TASK node output is
stored twice — twice in the CSV tree, twice in the Arrow sidecar, twice in HANA.

## Why it cannot simply be deleted

Counted over all 100 full workflow definitions on tenant-1:

* **15** workflows read ``$node.metadata.<field>`` (69 distinct expressions)
* **32** workflows read the flat spread (``$node.response_body``, ...)
* **0**  workflows read ``$node.metadata`` as a whole object

So the spread is the more-used half and stays; ``metadata`` goes — the reverse
of what ``wcp-node-output-storage-redesign.md`` §7 proposes, which would break
those 32. And because *every* surviving ``metadata`` read names a field, a path
alias covers 100 % of observed usage.

## The order these tests encode

``_rewrite_legacy_path`` runs ONLY after every strategy has failed
(``variable_resolver.py`` ~:906). So the alias is unreachable while the mirror
still exists, and becomes the safety net the instant it does not — off -> shadow
-> on, for free. Ship the alias first, watch it, then flip the write.
"""

from __future__ import annotations

import pytest

from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolver,
    _rewrite_legacy_path,
)


# ---------------------------------------------------------------------------
# 3a — the rewrite itself
# ---------------------------------------------------------------------------

def test_metadata_field_rewrites_to_the_bare_field():
    assert _rewrite_legacy_path(("metadata", "issue")) == ("issue",)
    assert _rewrite_legacy_path(("metadata", "response_body", "total")) == (
        "response_body",
        "total",
    )


def test_the_older_action_rewrite_still_wins():
    """``metadata.action -> _action`` predates this and must not be shadowed."""
    assert _rewrite_legacy_path(("metadata", "action")) == ("_action",)


def test_bare_metadata_is_not_rewritten():
    """0 of 100 tenant-1 workflows read the whole object, and rewriting it to
    the empty path would resolve to the node's ENTIRE output — a silent, wrong
    answer rather than an error."""
    assert _rewrite_legacy_path(("metadata",)) is None


def test_a_non_metadata_path_is_untouched():
    assert _rewrite_legacy_path(("response_body", "total")) is None
    assert _rewrite_legacy_path(()) is None


# ---------------------------------------------------------------------------
# 3a — end to end through the resolver
# ---------------------------------------------------------------------------

MIRRORED = {  # what a node output looks like TODAY
    "metadata": {"issue": "SA-1", "response_body": {"total": 7}},
    "issue": "SA-1",
    "response_body": {"total": 7},
}
MIRRORLESS = {  # what it looks like after 3c
    "issue": "SA-1",
    "response_body": {"total": 7},
}


@pytest.fixture
def resolver() -> VariableResolver:
    return VariableResolver([TraversalResolverStrategy()])


async def test_metadata_reads_survive_the_mirror_being_removed(resolver):
    ctx = {"n": MIRRORLESS}
    assert await resolver.resolve("{{$n.metadata.issue}}", ctx) == "SA-1"
    assert await resolver.resolve("{{$n.metadata.response_body.total}}", ctx) == 7


async def test_the_mirrored_shape_still_resolves_the_same_way(resolver):
    """The alias must be INERT while the mirror exists — same answers, and it is
    the direct path that produces them."""
    ctx = {"n": MIRRORED}
    assert await resolver.resolve("{{$n.metadata.issue}}", ctx) == "SA-1"
    assert await resolver.resolve("{{$n.issue}}", ctx) == "SA-1"
    assert await resolver.resolve("{{$n.metadata.response_body.total}}", ctx) == 7


async def test_a_handler_metadata_is_not_confused_with_a_task_mirror(resolver):
    """Non-TASK nodes write a genuine, small ``metadata`` through their handler
    (``{"type": "approval_flow"}``). It resolves directly and must never reach
    the rewrite."""
    ctx = {"n": {"metadata": {"type": "approval_flow"}, "approved": True}}
    assert await resolver.resolve("{{$n.metadata.type}}", ctx) == "approval_flow"


# ---------------------------------------------------------------------------
# 3c — the write, behind a default-on flag
# ---------------------------------------------------------------------------

def _task_output(mirror: bool, outputs: dict) -> dict:
    from app.layer2_application.features.complete_task.use_cases import (
        complete_task_use_case as ctu,
    )
    return ctu.build_task_output(outputs, output_reference="", mirror_metadata=mirror)


def test_mirror_on_is_byte_for_byte_todays_shape():
    out = {"issue": "SA-1", "n": 2}
    assert _task_output(True, out) == {"metadata": out, "issue": "SA-1", "n": 2}


def test_mirror_off_drops_the_duplicate_and_keeps_the_spread():
    out = {"issue": "SA-1", "n": 2}
    assert _task_output(False, out) == {"issue": "SA-1", "n": 2}


def test_a_worker_key_literally_named_metadata_survives_either_way():
    """Today ``update()`` overwrites the mirror with the worker's own value, so
    the worker wins. With the mirror off the worker's value is simply the only
    one. Same answer both ways — pinned so nobody "fixes" it into a regression.
    """
    out = {"metadata": {"mine": True}, "n": 1}
    assert _task_output(True, out)["metadata"] == {"mine": True}
    assert _task_output(False, out)["metadata"] == {"mine": True}


def test_empty_outputs_keep_the_envelope_shape():
    assert _task_output(True, {}) == {"metadata": {}}
    assert _task_output(False, {}) == {}
