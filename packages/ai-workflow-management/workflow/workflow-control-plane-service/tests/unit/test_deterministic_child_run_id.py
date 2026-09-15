"""Unit contract for ``deterministic_child_run_id``.

SA-846 Mode B: the id is keyed by (parent_run, node, generation, visit).
Redelivery within a visit dedupes to the same child run; a cycle
revisit (visit bump) mints a fresh one instead of colliding with visit
0's persisted row.
"""

from __future__ import annotations

import uuid

from app.layer1_domain.value_objects.event_id import EventId


def test_same_inputs_same_id():
    a = EventId.derive_child_run("run-1", "w", 0, visit=2)
    b = EventId.derive_child_run("run-1", "w", 0, visit=2)
    assert a == b


def test_visit_zero_keeps_legacy_shape():
    """visit=0 must hash the pre-visit name so existing in-flight child
    run ids (and replay dedupe against them) are unchanged."""
    legacy = EventId.derive_child_run("run-1", "w", 3)
    explicit = EventId.derive_child_run("run-1", "w", 3, visit=0)
    assert legacy == explicit


def test_each_visit_gets_fresh_id():
    ids = {
        EventId.derive_child_run("run-1", "w", 0, visit=v)
        for v in range(4)
    }
    assert len(ids) == 4


def test_generation_and_visit_are_independent_axes():
    gen_bump = EventId.derive_child_run("run-1", "w", 1, visit=0)
    visit_bump = EventId.derive_child_run("run-1", "w", 0, visit=1)
    assert gen_bump != visit_bump


def test_id_is_fixed_length_uuid():
    rid = EventId.derive_child_run("r" * 300, "w", 0, visit=7)
    assert len(rid) == 36
    uuid.UUID(rid)  # parses
