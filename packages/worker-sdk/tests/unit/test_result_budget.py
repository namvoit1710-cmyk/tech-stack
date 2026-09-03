"""The worker measures its OWN result before handing it to the executor.

Origin: run edda4f82-4a09-4587-beb5-8ed615ab8219 (tenant-1, 2026-08-18). An
http_request node returned a 12 980 452-byte result; the executor refused to
publish it past the 1 048 576-byte Event Mesh cap and the run failed with
RESULT_TOO_LARGE. The message the operator got named worker-side streaming
(SA-1944) as the fix — which for that payload would have saved 204 bytes of
12 980 452, because the oversized field was a ``str`` and streaming only
converts list/dict.

So the point of this module is attribution, not just a number: which field, how
many bytes, and whether streaming could ever have moved it.
"""

from __future__ import annotations

import json

from worker_sdk.layer2_application.services.result_budget import (
    budget_verdict,
    measure_result,
)


def test_within_budget_has_no_verdict():
    assert budget_verdict({"a": "x" * 10}, 1024) is None


def test_empty_or_missing_outputs_have_no_verdict():
    assert budget_verdict({}, 1024) is None
    assert budget_verdict(None, 1024) is None


def test_measure_reports_total_and_sorts_fields_biggest_first():
    outputs = {"small": "a", "huge": "b" * 5000, "mid": "c" * 500}
    total, fields = measure_result(outputs)

    assert total == len(json.dumps(outputs, default=str).encode("utf-8"))
    assert [f["field"] for f in fields] == ["huge", "mid", "small"]
    assert fields[0]["bytes"] > fields[1]["bytes"] > fields[2]["bytes"]


def test_a_string_field_is_marked_not_streamable():
    """The whole reason edda4f82 was misdiagnosed."""
    _, fields = measure_result({"resolved_body": "x" * 5000})

    assert fields[0]["kind"] == "str"
    assert fields[0]["streamable"] is False


def test_list_and_dict_fields_are_marked_streamable():
    _, fields = measure_result({
        "rows": [{"a": 1}, {"a": 2}],
        "obj": {"k": "v"},
    })
    by_name = {f["field"]: f for f in fields}

    assert by_name["rows"]["streamable"] is True
    assert by_name["obj"]["streamable"] is True


def test_a_file_ref_is_already_a_pointer_not_something_to_stream():
    _, fields = measure_result({
        "out": {"__file_ref": True, "file_id": "file_1", "row_count": 10},
    })

    assert fields[0]["kind"] == "file_ref"
    assert fields[0]["streamable"] is False


def test_verdict_names_the_field_its_size_and_why_it_could_not_stream():
    outputs = {
        "resolved_body": "x" * 5000,
        "response_body": {"file_id": "f1"},
    }
    msg = budget_verdict(outputs, 1024)

    assert msg is not None
    assert "resolved_body" in msg
    assert "5" in msg                      # its byte count appears
    assert "NOT-STREAMABLE" in msg
    assert "1024" in msg                   # the budget it broke
    # The executor truncates a worker error at 500 chars; the diagnostic has to
    # survive that or it is useless exactly when it matters.
    assert len(msg) <= 500


def test_verdict_survives_a_non_serializable_output_without_raising():
    """Cannot measure is not the same as too large - never fail on a guess."""
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    # default=str makes this measurable; the contract is only that it does not
    # raise and does not invent a failure.
    assert budget_verdict({"x": Opaque()}, 1024) is None


def test_non_dict_outputs_are_measured_as_a_single_root_field():
    total, fields = measure_result(["a" * 5000])

    assert total > 5000
    assert len(fields) == 1
    assert fields[0]["field"] == "<root>"


def test_field_list_is_capped_so_the_message_stays_bounded():
    outputs = {f"f{i}": "x" * 900 for i in range(40)}
    msg = budget_verdict(outputs, 1024)

    assert msg is not None
    assert len(msg) <= 500
