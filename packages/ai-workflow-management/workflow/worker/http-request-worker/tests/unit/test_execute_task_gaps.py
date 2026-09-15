"""Gap-fill unit tests for ExecuteTaskUseCase — branches not covered by
test_execute_task.py.

Grounded in execute_task_usecase.py source:
- invalid HTTP method branch (raises ValueError, caught -> ERROR)  [invalid input]
- timeout out-of-range branch (< 1 and > 300)                      [boundary]
- credential decides auth: no credential -> unauthenticated; cred wins over auth_type
- httpx exception carrying a .response -> error_outputs populated  [failure path]
- ReadTimeout / generic exception -> error_type recorded           [failure path]
- body_type=file: _download_file + _unwrap_multipart via httpx.get [happy/edge]
- monitor metric emission (requests + success/failure/error)       [happy/edge]

All HTTP is mocked at the module seam; no real network.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
    _unwrap_multipart,
)

_UC = "app.layer2_application.features.execute_task.use_cases.execute_task_usecase"


def _mock_response(status_code=200, headers=None, text="OK", json_data=None,
                   url="https://example.com", content=b""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "text/plain"}
    resp.text = text
    resp.url = url
    resp.content = content
    _b = content if isinstance(content, (bytes, bytearray)) else b''
    if json_data is not None:
        import json as _json; _b = _json.dumps(json_data).encode()
    resp.iter_raw = MagicMock(return_value=[_b])
    resp.iter_bytes = MagicMock(return_value=[_b])
    resp.elapsed = timedelta(milliseconds=5)
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = Exception("not json")
    return resp


def _client_mock(response=None, side_effect=None):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    if side_effect is not None:
        client.stream.side_effect = side_effect
    else:
        client.stream.return_value.__enter__.return_value = response
    return client


class TestMethodValidation:
    def test_invalid_method_returns_error(self, logger, monitor):
        # Invalid method is rejected BEFORE any client is built.
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
            result = uc.execute(ExecuteTaskCommand(
                task_id="m1",
                action="http_request",
                inputs={"method": "FETCH", "url": "https://example.com"},
            ))
        assert result.status == TaskStatus.ERROR
        assert "Invalid HTTP method: 'FETCH'" in result.error
        assert result.outputs["error_type"] == "ValueError"
        assert result.outputs["is_success"] is False
        cls.assert_not_called()

    @patch(f"{_UC}.httpx.Client")
    def test_lowercase_method_is_upcased_and_accepted(self, cls, logger, monitor):
        cls.return_value = _client_mock(_mock_response(200))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="m2",
            action="http_request",
            inputs={"method": "post", "url": "https://example.com"},
        ))
        assert result.status == TaskStatus.SUCCESS
        assert cls.return_value.stream.call_args.kwargs["method"] == "POST"


class TestTimeoutBoundary:
    def test_timeout_zero_below_min_rejected(self, logger, monitor):
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
            result = uc.execute(ExecuteTaskCommand(
                task_id="to1",
                action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": 0},
            ))
        assert result.status == TaskStatus.ERROR
        assert "between 1 and 300" in result.error
        cls.assert_not_called()

    def test_timeout_above_max_rejected(self, logger, monitor):
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
            result = uc.execute(ExecuteTaskCommand(
                task_id="to2",
                action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": 301},
            ))
        assert result.status == TaskStatus.ERROR
        assert "between 1 and 300" in result.error
        cls.assert_not_called()

    @patch(f"{_UC}.httpx.Client")
    def test_timeout_at_min_boundary_accepted(self, cls, logger, monitor):
        cls.return_value = _client_mock(_mock_response(200))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="to3",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com",
                    "timeout_seconds": 1},
        ))
        assert result.status == TaskStatus.SUCCESS
        assert cls.return_value.stream.call_args.kwargs["timeout"] == 1

    @patch(f"{_UC}.httpx.Client")
    def test_timeout_at_max_boundary_accepted(self, cls, logger, monitor):
        cls.return_value = _client_mock(_mock_response(200))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="to4",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com",
                    "timeout_seconds": 300},
        ))
        assert result.status == TaskStatus.SUCCESS
        assert cls.return_value.stream.call_args.kwargs["timeout"] == 300

    @patch(f"{_UC}.httpx.Client")
    def test_non_numeric_timeout_falls_back_to_default_30(self, cls, logger, monitor):
        cls.return_value = _client_mock(_mock_response(200))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="to5",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com",
                    "timeout_seconds": "not-a-number"},
        ))
        assert result.status == TaskStatus.SUCCESS
        assert cls.return_value.stream.call_args.kwargs["timeout"] == 30


class TestCredentialDecidesAuth:
    """http-request has no auth_type field — the selected credential decides auth."""

    def test_no_credential_sends_unauthenticated(self, logger, monitor):
        # No credential -> HTTP auth is optional -> request goes out unauthenticated
        # (public endpoints). The worker no longer fails closed on a phantom auth hint.
        with patch(f"{_UC}.httpx.Client") as cls:
            cls.return_value = _client_mock(response=_mock_response(200))
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
            result = uc.execute(ExecuteTaskCommand(
                task_id="fa1",
                action="http_request",
                inputs={"method": "GET", "url": "https://api.example.com"},
            ))
        assert result.status == TaskStatus.SUCCESS
        cls.assert_called_once()
        assert "Authorization" not in cls.return_value.stream.call_args.kwargs["headers"]

    def test_credential_applied_even_when_stale_auth_type_none(self, logger, monitor):
        # Regression (the 401 bug): a selected credential must be applied even if a stale
        # auth_type="none" rides along in the inputs (e.g. an older saved workflow). The
        # credential is the source of truth; auth_type never suppresses it.
        with patch(f"{_UC}.httpx.Client") as cls:
            cls.return_value = _client_mock(response=_mock_response(200))
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
            result = uc.execute(ExecuteTaskCommand(
                task_id="fa2",
                action="http_request",
                inputs={
                    "method": "GET",
                    "url": "https://api.example.com/me",
                    "auth_type": "none",  # stale hint — must NOT suppress the credential
                    "credential_ref": "c1",
                    "__credentials": {"c1": {"type": "bearer", "config": {"token": "jwt"}}},
                },
            ))
        assert result.status == TaskStatus.SUCCESS
        auth = cls.return_value.stream.call_args.kwargs["headers"]["Authorization"]
        assert auth == "Bearer jwt"

    def test_unapplicable_credential_type_fails_instead_of_going_out_bare(self, logger, monitor):
        # Regression, same family as the one above but one layer down. Every vault
        # adapter built on JsonBlobAdapterBase resolved to type "json", which matched
        # none of the bearer/basic/api_key branches — so _apply_effective_auth fell off
        # the end, set no header, and the request went out UNAUTHENTICATED. The node
        # then reported the remote's 401, which reads as the remote's fault.
        #
        # "A credential was selected" and "the request was authenticated" must not be
        # allowed to disagree silently. An unapplicable type is now a task error.
        with patch(f"{_UC}.httpx.Client") as cls:
            cls.return_value = _client_mock(response=_mock_response(200))
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
            result = uc.execute(ExecuteTaskCommand(
                task_id="fa3",
                action="http_request",
                inputs={
                    "method": "GET",
                    "url": "https://api.example.com/me",
                    "credential_ref": "c1",
                    "__credentials": {
                        "c1": {"type": "json", "config": {"clientId": "c", "clientSecret": "s"}}
                    },
                },
            ))
        assert result.status == TaskStatus.ERROR
        # The request must never have been sent — failing closed beats leaking an
        # unauthenticated call to a third party.
        cls.return_value.stream.assert_not_called()
        assert "json" in str(result.error or result.outputs)


class TestFailurePaths:
    @patch(f"{_UC}.httpx.Client")
    def test_read_timeout_is_captured_as_error_type(self, cls, logger, monitor):
        cls.return_value = _client_mock(side_effect=httpx.ReadTimeout("timed out"))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="fp1",
            action="http_request",
            inputs={"method": "GET", "url": "https://slow.example.com"},
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["error_type"] == "ReadTimeout"
        assert result.outputs["is_success"] is False
        assert ("http_request_task_error", 1) in monitor.tracked

    @patch(f"{_UC}.httpx.Client")
    def test_exception_carrying_response_populates_error_outputs(self, cls, logger, monitor):
        # An httpx exception can carry a partial .response (e.g. HTTPStatusError).
        partial = _mock_response(
            502,
            headers={"content-type": "application/json"},
            json_data={"err": "bad gateway"},
            url="https://api.example.com/x",
        )
        exc = httpx.HTTPStatusError("boom", request=MagicMock(), response=partial)
        cls.return_value = _client_mock(side_effect=exc)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="fp2",
            action="http_request",
            inputs={"method": "GET", "url": "https://api.example.com/x"},
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["status_code"] == 502
        assert result.outputs["content_type"] == "application/json"
        assert result.outputs["final_url"] == "https://api.example.com/x"
        assert result.outputs["response_body"] == {"err": "bad gateway"}
        assert result.outputs["error_type"] == "HTTPStatusError"

    @patch(f"{_UC}.httpx.Client")
    def test_response_body_parse_failure_in_error_path_swallowed(self, cls, logger, monitor):
        # If parsing the partial response body itself throws, response_body -> "".
        partial = _mock_response(500, url="https://api.example.com/y")
        # Force _parse_response_body to blow up: .text raises, json content-type.
        partial.headers = {"content-type": "application/json"}
        partial.json.side_effect = Exception("decode")
        type(partial).text = property(lambda self: (_ for _ in ()).throw(RuntimeError("no text")))
        exc = httpx.HTTPStatusError("boom", request=MagicMock(), response=partial)
        cls.return_value = _client_mock(side_effect=exc)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="fp3",
            action="http_request",
            inputs={"method": "GET", "url": "https://api.example.com/y"},
        ))
        assert result.status == TaskStatus.ERROR
        assert result.outputs["response_body"] == ""
        assert result.outputs["status_code"] == 500


class TestFileBody:
    @patch(f"{_UC}.httpx.get")
    @patch(f"{_UC}.httpx.Client")
    def test_body_type_file_downloads_and_sends_bytes(self, cls, mock_get, logger, monitor):
        cls.return_value = _client_mock(_mock_response(200))
        # File service returns raw (non-multipart) bytes here.
        dl = _mock_response(200, headers={"content-type": "application/octet-stream"},
                            content=b"file-bytes")
        dl.raise_for_status = MagicMock()
        mock_get.return_value = dl

        resolver = MagicMock()
        resolver._base_url = "http://file-svc"
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, file_ref_resolver=resolver)
        result = uc.execute(ExecuteTaskCommand(
            task_id="file1",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com/upload",
                    "body_type": "file", "body_file": "abc123"},
        ))
        assert result.status == TaskStatus.SUCCESS
        # bytes body sent verbatim; octet-stream content-type defaulted.
        sent = cls.return_value.stream.call_args.kwargs
        assert sent["content"] == b"file-bytes"
        assert sent["headers"]["Content-Type"] == "application/octet-stream"
        mock_get.assert_called_once_with("http://file-svc/api/v1/download/abc123", timeout=60.0)

    def test_body_type_file_without_resolver_errors(self, logger, monitor):
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)  # no resolver
            result = uc.execute(ExecuteTaskCommand(
                task_id="file2",
                action="http_request",
                inputs={"method": "POST", "url": "https://example.com/upload",
                        "body_type": "file", "body_file": "abc123"},
            ))
        assert result.status == TaskStatus.ERROR
        assert "file_ref_resolver not configured" in result.error
        cls.assert_not_called()

    @patch(f"{_UC}.httpx.Client")
    def test_body_type_file_empty_id_sends_no_body(self, cls, logger, monitor):
        cls.return_value = _client_mock(_mock_response(200))
        resolver = MagicMock()
        resolver._base_url = "http://file-svc"
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, file_ref_resolver=resolver)
        result = uc.execute(ExecuteTaskCommand(
            task_id="file3",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com/upload",
                    "body_type": "file", "body_file": ""},
        ))
        assert result.status == TaskStatus.SUCCESS
        assert cls.return_value.stream.call_args.kwargs["content"] is None


class TestUnwrapMultipart:
    def test_passthrough_when_not_multipart(self):
        raw = b'{"plain":"json"}'
        assert _unwrap_multipart(raw, "application/json") == raw

    def test_unwraps_single_part_envelope(self):
        boundary = b"--BOUNDARY"
        raw = (
            boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b"THE-FILE-BYTES"
            b"\r\n" + boundary + b"--\r\n"
        )
        assert _unwrap_multipart(raw, "multipart/form-data") == b"THE-FILE-BYTES"

    def test_unwraps_by_leading_delimiter_when_content_type_omits_boundary(self):
        boundary = b"--xyz"
        raw = (
            boundary + b"\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"payload"
            b"\r\n" + boundary + b"--"
        )
        # Content-type does not start with multipart/ but raw opens with "--".
        assert _unwrap_multipart(raw, "") == b"payload"

    def test_no_header_terminator_returns_raw(self):
        raw = b"--b\r\nno blank line ever"
        assert _unwrap_multipart(raw, "multipart/form-data") == raw

    def test_no_crlf_returns_raw(self):
        raw = b"--justdashesnobreak"
        assert _unwrap_multipart(raw, "multipart/form-data") == raw


class TestPureHelpers:
    """Direct unit coverage of pure branch logic (no HTTP)."""

    def _uc(self, logger, monitor):
        return ExecuteTaskUseCase(logger=logger, monitor=monitor)

    # -- _coerce_value ------------------------------------------------------
    def test_coerce_none_and_empty_passthrough(self, logger, monitor):
        uc = self._uc(logger, monitor)
        assert uc._coerce_value(None, "number") is None
        assert uc._coerce_value("", "number") == ""

    def test_coerce_number_float_fallback(self, logger, monitor):
        uc = self._uc(logger, monitor)
        # int() fails, float() succeeds.
        assert uc._coerce_value("3.14", "number") == 3.14

    def test_coerce_number_unparseable_returns_raw(self, logger, monitor):
        uc = self._uc(logger, monitor)
        # both int() and float() fail -> raw string returned.
        assert uc._coerce_value("abc", "number") == "abc"

    def test_coerce_boolean_already_bool(self, logger, monitor):
        uc = self._uc(logger, monitor)
        assert uc._coerce_value(True, "boolean") is True

    def test_coerce_object_invalid_json_returns_raw(self, logger, monitor):
        uc = self._uc(logger, monitor)
        # invalid JSON -> JSONDecodeError caught -> raw returned.
        assert uc._coerce_value("{not json", "object") == "{not json"

    def test_coerce_object_non_string_passthrough(self, logger, monitor):
        uc = self._uc(logger, monitor)
        assert uc._coerce_value({"a": 1}, "object") == {"a": 1}

    # -- _resolve_body fallbacks --------------------------------------------
    def test_resolve_body_fields_non_list_coerced_to_empty(self, logger, monitor):
        uc = self._uc(logger, monitor)
        body = uc._resolve_body(
            {"body_type": "structured", "body_fields": "not-a-list"}
        )
        assert body == {}

    def test_resolve_body_unknown_type_falls_back_to_body(self, logger, monitor):
        uc = self._uc(logger, monitor)
        # An unrecognized body_type past the known set falls through to `body`.
        body = uc._resolve_body({"body_type": "weird", "body": '{"k": 1}'})
        assert body == {"k": 1}

    # -- _to_bool default ---------------------------------------------------
    def test_to_bool_non_string_non_bool_returns_default(self, logger, monitor):
        uc = self._uc(logger, monitor)
        assert uc._to_bool(object(), default=True) is True
        assert uc._to_bool(123, default=False) is False

    # -- _stringify_body ----------------------------------------------------
    def test_stringify_empty_returns_empty(self, logger, monitor):
        uc = self._uc(logger, monitor)
        assert uc._stringify_body(None) == ""
        assert uc._stringify_body("") == ""

    def test_stringify_unserializable_dict_falls_back_to_str(self, logger, monitor):
        uc = self._uc(logger, monitor)
        # A dict json.dumps can't serialize -> TypeError -> str(body).
        weird = {"k": object()}
        out = uc._stringify_body(weird)
        assert "object at 0x" in out

    def test_stringify_truncates_long_body(self, logger, monitor):
        uc = self._uc(logger, monitor)
        out = uc._stringify_body("x" * 2000, limit=1000)
        assert out.endswith("… (truncated)")
        assert len(out) <= 1000 + len("… (truncated)")


class TestMonitorMetrics:
    @patch(f"{_UC}.httpx.Client")
    def test_success_tracks_requests_and_success(self, cls, logger, monitor):
        cls.return_value = _client_mock(_mock_response(200))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="mt1", action="http_request",
            inputs={"method": "GET", "url": "https://example.com"},
        ))
        metrics = [m for m, _ in monitor.tracked]
        assert "http_request_task_requests" in metrics
        assert "http_request_task_success" in metrics
        assert "http_request_task_duration_ms" in metrics

    @patch(f"{_UC}.httpx.Client")
    def test_non_2xx_tracks_failure(self, cls, logger, monitor):
        cls.return_value = _client_mock(_mock_response(404, url="https://example.com/x"))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="mt2", action="http_request",
            inputs={"method": "GET", "url": "https://example.com/x"},
        ))
        metrics = [m for m, _ in monitor.tracked]
        assert "http_request_task_failure" in metrics
