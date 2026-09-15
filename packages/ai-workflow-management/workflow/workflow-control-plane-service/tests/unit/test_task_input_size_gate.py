"""Size gate for the task-input claim-check (node_execution_runner).

A small resolved input rides the dispatch INLINE with full fidelity; only a
genuinely large input is offloaded to a file. This guards the regression where
the *unconditional* CSV claim-check split an HTTP node's array fields
(``headers`` / a ``body`` bound to a nested payload) into artifacts that the
``{file_id, data_mode}`` pointer could not carry — so the worker received no
``body`` and ``resolved_body`` came back null.

Integration (small→inline / large→offloaded through ``_exec_uniform``) is
covered end-to-end by the live re-run of the affected workflow; here we pin the
gate's decision boundary, which is what the claim-check condition keys on.
"""
from app.layer2_application.orchestration.node_execution_runner import (
    _TASK_INPUT_CLAIM_CHECK_BYTES,
    _approx_input_bytes,
)


def test_threshold_is_a_sane_message_safe_size():
    # Comfortably below the ~900 KB Event Mesh payload cap (dispatch rides inline
    # under the gate), and large enough that real node inputs stay inline.
    assert 16 * 1024 <= _TASK_INPUT_CLAIM_CHECK_BYTES <= 900_000


def test_the_reported_http_input_is_below_the_gate():
    """The exact shape that regressed — an HTTP node whose ``body`` is a nested
    MDG payload plus a ``headers`` array — is small, so it must stay inline
    (never claim-checked, never split into droppable artifacts)."""
    payload = {
        "url": "https://api/convert", "method": "POST", "body_type": "raw_json",
        "headers": [{"header_name": "Content-Type", "header_value": "application/json"}],
        "body": [{"deepData": {
            "product": "", "productType": "VERP",
            "to_Plant": [{"plant": "1000", "to_ProductPlantProcurement": {"plant": "1000"}}],
            "to_Description": [{"language": "EN", "productDescription": "VERP1"}],
        }}],
    }
    assert _approx_input_bytes(payload) < _TASK_INPUT_CLAIM_CHECK_BYTES


def test_large_input_crosses_the_gate():
    payload = {"url": "https://api", "blob": "x" * (_TASK_INPUT_CLAIM_CHECK_BYTES + 1)}
    assert _approx_input_bytes(payload) > _TASK_INPUT_CLAIM_CHECK_BYTES


def test_approx_bytes_is_never_fatal_on_unserializable():
    class Weird:
        pass
    # default=str keeps it serializable; a truly hostile object still returns an int.
    assert isinstance(_approx_input_bytes({"x": Weird()}), int)
    assert _approx_input_bytes({}) == len("{}".encode("utf-8"))
