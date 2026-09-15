"""A handed-over collection is serialised INTO the request, not into RAM first.

Step 3 of streaming a file-backed body. The SDK now hands this worker a
``StreamedCollection`` instead of a list (step 2), and the windowed join behind it
holds one window at a time (step 1). This is where that becomes an actual saving:
``_serialize_body`` returns a generator, httpx sends it chunked, and the whole
collection is never a Python object nor a bytes blob.

The digest that PR #1923 put on ``resolved_body`` is computed AS the body streams
— size, sha256 and the 4 KB prefix all fall out of bytes that were going to be
produced anyway. So the node still reports what it sent, at no extra memory.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    StreamedCollection,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskUseCase,
)

from tests.unit.test_execute_task import _mock_response


class _FakeResolver:
    """Stands in for the SDK resolver; the handle only delegates to it."""

    def __init__(self, objs):
        self._objs = objs
        self.iterations = 0

    def iter_objects(self, ref, window_size):
        self.iterations += 1
        return iter(self._objs)


def _handle(objs):
    return StreamedCollection(_FakeResolver(objs), {"file_id": "f1", "row_count": len(objs)}, 500)


def _client(mock_client_cls):
    """A client mock that CONSUMES the request body, like httpx does.

    A mock that never reads ``content`` would let a generator body look fine
    while nothing was ever serialised — and the digest, which is measured on the
    way out, would silently read zero.
    """
    c = MagicMock()
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=False)

    resp_cm = MagicMock()
    resp_cm.__enter__ = MagicMock(return_value=_mock_response(
        200, headers={"content-type": "application/json"}, json_data={"ok": True},
    ))
    resp_cm.__exit__ = MagicMock(return_value=False)

    def _stream(**kwargs):
        content = kwargs.get("content")
        if content is not None and hasattr(content, "__iter__") and not isinstance(content, (bytes, str)):
            c.sent = b"".join(content)  # sending IS reading the body
        return resp_cm

    c.stream = MagicMock(side_effect=_stream)
    mock_client_cls.return_value = c
    return c


def _run(uc, body):
    return uc.execute(ExecuteTaskCommand(
        task_id="t-stream", action="http_request",
        inputs={"method": "POST", "url": "https://example.com",
                "body_type": "raw_json", "body": body},
    ))


_OBJS = [{"id": i, "name": f"row-{i}"} for i in range(50)]
_EXPECTED = json.dumps(_OBJS, separators=(",", ":")).encode("utf-8")


@patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
def test_the_body_is_sent_as_a_generator_not_a_bytes_blob(mock_client_cls, logger, monitor):
    client = _client(mock_client_cls)
    result = _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), _handle(_OBJS))

    assert result.status == TaskStatus.SUCCESS
    content = client.stream.call_args.kwargs["content"]
    assert not isinstance(content, (bytes, str)), "a blob defeats the whole point"
    assert hasattr(content, "__iter__")


@patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
def test_the_streamed_bytes_are_the_same_json_as_the_materialised_list(
    mock_client_cls, logger, monitor,
):
    """Parity again: streaming must change the memory profile, not the payload."""
    client = _client(mock_client_cls)
    _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), _handle(_OBJS))

    sent = b"".join(client.stream.call_args.kwargs["content"])
    assert json.loads(sent.decode("utf-8")) == _OBJS
    assert sent == _EXPECTED


@patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
def test_an_empty_collection_still_sends_a_valid_json_array(mock_client_cls, logger, monitor):
    client = _client(mock_client_cls)
    _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), _handle([]))

    sent = b"".join(client.stream.call_args.kwargs["content"])
    assert sent == b"[]"


@patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
def test_the_content_type_is_json(mock_client_cls, logger, monitor):
    client = _client(mock_client_cls)
    _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), _handle(_OBJS))

    headers = client.stream.call_args.kwargs["headers"]
    assert headers.get("Content-Type") == "application/json"


class TestTheDigestFallsOutOfTheStream:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_it_reports_the_full_size_and_hash_of_what_was_sent(
        self, mock_client_cls, logger, monitor,
    ):
        _client(mock_client_cls)
        result = _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), _handle(_OBJS))
        out = result.outputs

        assert out["resolved_body_bytes"] == len(_EXPECTED)
        assert out["resolved_body_sha256"] == hashlib.sha256(_EXPECTED).hexdigest()

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_a_large_stream_carries_only_the_prefix(self, mock_client_cls, logger, monitor):
        _client(mock_client_cls)
        big = [{"id": i, "blob": "x" * 200} for i in range(200)]
        result = _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), _handle(big))
        out = result.outputs

        assert out["resolved_body_truncated"] is True
        assert len(out["resolved_body"].encode("utf-8")) <= 4096
        assert out["resolved_body_bytes"] > 4096

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_a_small_stream_is_not_marked_truncated(self, mock_client_cls, logger, monitor):
        _client(mock_client_cls)
        result = _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), _handle([{"a": 1}]))
        out = result.outputs

        assert out["resolved_body_truncated"] is False
        assert out["resolved_body"] == '[{"a":1}]'
        assert out["resolved_body_bytes"] == len(b'[{"a":1}]')


@patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
def test_an_ordinary_body_is_completely_unaffected(mock_client_cls, logger, monitor):
    """Regression fence: everything that is not a handle keeps its old path."""
    client = _client(mock_client_cls)
    result = _run(ExecuteTaskUseCase(logger=logger, monitor=monitor), {"plain": True})

    assert result.status == TaskStatus.SUCCESS
    assert client.stream.call_args.kwargs["content"] == json.dumps({"plain": True}).encode("utf-8")
    assert result.outputs["resolved_body"] == json.dumps({"plain": True})
    assert result.outputs["resolved_body_truncated"] is False
