import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.http_method import HttpMethod
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.body_type import BodyType
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
    HTTP_REQUEST_INPUT_SCHEMA,
    HTTP_REQUEST_OUTPUT_SCHEMA,
)


def _mock_response(status_code=200, headers=None, text="OK", json_data=None, url="https://example.com"):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "text/plain"}
    resp.text = text
    resp.url = url
    resp.elapsed = timedelta(milliseconds=42)
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = Exception("not json")
    # SA-1940 — the use-case STREAMS the body via iter_bytes() + a size cap.
    if json_data is not None:
        _body = json.dumps(json_data).encode("utf-8")
    elif isinstance(text, bytes):
        _body = text
    else:
        _body = (text or "").encode("utf-8")
    resp.iter_bytes = MagicMock(return_value=[_body])
    resp.iter_raw = MagicMock(return_value=[_body])
    return resp


class TestGetRequest:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_simple_get(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200, text="hello")
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t1",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com/api"},
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["status_code"] == 200
        assert result.outputs["is_success"] is True
        assert result.outputs["response_body"] == "hello"
        mock_client.stream.assert_called_once()

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_get_with_query_params(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t2",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com/search",
                "query_params": {"q": "test", "page": "1"},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["params"] == {"q": "test", "page": "1"}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_get_no_query_params_passes_none_to_preserve_inline_query(
        self, mock_client_cls, logger, monitor
    ):
        # Regression: httpx REPLACES the URL's inline query with ``params`` whenever it is
        # not None — even an empty ``{}`` wipes it. With no query-param rows, ``params`` must
        # be None so a URL that carries its own query string keeps it (a
        # ".../forecast?latitude=..&longitude=.." call must not lose its coords).
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t2b",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://api.example.com/forecast?latitude=13.98&longitude=108.0",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert mock_client.stream.call_args.kwargs["params"] is None

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_get_empty_query_params_list_passes_none(
        self, mock_client_cls, logger, monitor
    ):
        # The list-of-pairs form that resolves to an empty mapping must also become None,
        # not ``{}`` — same query-stripping trap as the dict case.
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t2c",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://api.example.com/search?q=hi",
                "query_params": [],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert mock_client.stream.call_args.kwargs["params"] is None


class TestPostRequest:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_post_with_json_body(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(
            201,
            headers={"content-type": "application/json"},
            json_data={"id": 42},
        )
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t3",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com/items",
                "body": {"name": "widget"},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["status_code"] == 201
        assert result.outputs["response_body"] == {"id": 42}
        # Verify body was serialized as JSON bytes
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == json.dumps({"name": "widget"}).encode("utf-8")

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_post_with_string_body(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t4",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com/raw",
                "body": "raw text body",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == b"raw text body"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_post_with_list_body(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t4b",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com/batch",
                "body": [1, 2, 3],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == json.dumps([1, 2, 3]).encode("utf-8")

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_post_with_no_body(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(204)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t5",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com/empty"},
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] is None


class TestAuthentication:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_bearer_auth(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t6",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://api.example.com/me",
                "auth_type": "bearer",
                "credential_ref": "c1",
                "__credentials": {"c1": {"type": "bearer",
                                         "config": {"token": "my-jwt-token"}}},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer my-jwt-token"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_basic_auth(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t7",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://api.example.com/me",
                "auth_type": "basic",
                "credential_ref": "c1",
                "__credentials": {"c1": {"type": "basic",
                                         "config": {"username": "user", "password": "pass"}}},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        expected = base64.b64encode(b"user:pass").decode()
        assert call_kwargs.kwargs["headers"]["Authorization"] == f"Basic {expected}"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_api_key_auth(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t8",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://api.example.com/data",
                "auth_type": "api_key",
                "credential_ref": "c1",
                "__credentials": {"c1": {"type": "api_key",
                                         "config": {"value": "key-12345",
                                                    "header": "X-Custom-Key"}}},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["headers"]["X-Custom-Key"] == "key-12345"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_api_key_auth_default_header(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t8b",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://api.example.com/data",
                "auth_type": "api_key",
                "credential_ref": "c1",
                "__credentials": {"c1": {"type": "api_key",
                                         "config": {"value": "key-12345"}}},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["headers"]["X-API-Key"] == "key-12345"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_no_auth(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t9",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "auth_type": "none",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert "Authorization" not in call_kwargs.kwargs["headers"]


class TestResponseParsing:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_json_response(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            json_data={"key": "value"},
        )
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t10",
            action="http_request",
            inputs={"method": "GET", "url": "https://api.example.com/data"},
        ))

        assert result.outputs["response_body"] == {"key": "value"}
        assert "application/json" in result.outputs["content_type"]

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_json_parse_failure_falls_back_to_text(self, mock_client_cls, logger, monitor):
        resp = _mock_response(
            200,
            headers={"content-type": "application/json"},
            text="not valid json",
        )
        resp.json.side_effect = Exception("json decode error")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = resp
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t11",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com/bad-json"},
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["response_body"] == "not valid json"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_text_response(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(
            200,
            headers={"content-type": "text/html"},
            text="<h1>Hello</h1>",
        )
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t12",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com"},
        ))

        assert result.outputs["response_body"] == "<h1>Hello</h1>"


class TestErrorHandling:
    def test_missing_url_returns_error(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t13",
            action="http_request",
            inputs={"method": "GET"},
        ))

        assert result.status == TaskStatus.ERROR
        assert "'url' is required" in result.error

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_connection_error(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t14",
            action="http_request",
            inputs={"method": "GET", "url": "https://unreachable.example.com"},
        ))

        assert result.status == TaskStatus.ERROR
        assert result.error is not None

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_4xx_returns_error_with_is_success_false(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(
            404, text="Not Found", url="https://example.com/missing"
        )
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t15",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com/missing"},
        ))

        assert result.status == TaskStatus.ERROR
        # Error message carries status, method, final URL, and response body.
        assert "HTTP 404" in result.error
        assert "GET" in result.error
        assert "https://example.com/missing" in result.error
        assert "Not Found" in result.error
        assert result.outputs["status_code"] == 404
        assert result.outputs["is_success"] is False

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_5xx_returns_error_with_is_success_false(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(
            500, text="Internal Server Error", url="https://example.com/error"
        )
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t16",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com/error"},
        ))

        assert result.status == TaskStatus.ERROR
        assert "HTTP 500" in result.error
        assert "https://example.com/error" in result.error
        assert "Internal Server Error" in result.error
        assert result.outputs["status_code"] == 500
        assert result.outputs["is_success"] is False

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_error_message_includes_json_response_body(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(
            400,
            headers={"content-type": "application/json"},
            json_data={"error": "invalid_field", "detail": "name is required"},
            url="https://api.example.com/users",
        )
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t17",
            action="http_request",
            inputs={"method": "POST", "url": "https://api.example.com/users"},
        ))

        assert result.status == TaskStatus.ERROR
        assert "HTTP 400" in result.error
        assert "POST" in result.error
        assert "https://api.example.com/users" in result.error
        # The JSON error body is serialized into the message.
        assert "invalid_field" in result.error
        assert "name is required" in result.error


class TestClientOptions:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_custom_headers(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t17",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "headers": {"Accept": "text/plain", "X-Custom": "value"},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["headers"]["Accept"] == "text/plain"
        assert call_kwargs.kwargs["headers"]["X-Custom"] == "value"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_ssl_and_redirect_options(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="t18",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "verify_ssl": False,
                "follow_redirects": False,
            },
        ))

        mock_client_cls.assert_called_once_with(verify=False, follow_redirects=False)

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_default_method_is_get(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="t19",
            action="http_request",
            inputs={"url": "https://example.com"},
        ))

        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["method"] == "GET"


class TestSchemas:
    def test_input_schema_structure(self):
        assert isinstance(HTTP_REQUEST_INPUT_SCHEMA, list)
        keys = [f["key"] for f in HTTP_REQUEST_INPUT_SCHEMA]
        assert "method" in keys
        assert "url" in keys
        # method and url have required validation rules in fieldConfig
        field_map = {f["key"]: f for f in HTTP_REQUEST_INPUT_SCHEMA}
        method_rules = field_map["method"]["fieldConfig"].get("rules", [])
        assert any(r["method"] == "required" for r in method_rules)
        url_rules = field_map["url"]["fieldConfig"].get("rules", [])
        assert any(r["method"] == "required" for r in url_rules)

    def test_output_schema_structure(self):
        assert isinstance(HTTP_REQUEST_OUTPUT_SCHEMA, list)
        keys = [f["key"] for f in HTTP_REQUEST_OUTPUT_SCHEMA]
        assert "status_code" in keys
        assert "is_success" in keys
        field_map = {f["key"]: f for f in HTTP_REQUEST_OUTPUT_SCHEMA}
        status_rules = field_map["status_code"]["fieldConfig"].get("rules", [])
        assert any(r["method"] == "required" for r in status_rules)

    def test_headers_schema_is_array_with_wrapper(self):
        field_map = {f["key"]: f for f in HTTP_REQUEST_INPUT_SCHEMA}
        headers = field_map["headers"]
        assert headers["outputType"] == "array"
        assert "SEQUENCE" in str(headers["fieldConfig"]["fieldWrapper"])

    def test_body_type_select_field_exists(self):
        field_map = {f["key"]: f for f in HTTP_REQUEST_INPUT_SCHEMA}
        assert "body_type" in field_map
        bt = field_map["body_type"]
        assert bt["fieldConfig"]["fieldControl"] == "SelectControl"
        option_ids = [o["id"] for o in bt["fieldConfig"]["controlProps"]["options"]]
        assert "none" in option_ids
        assert "structured" in option_ids
        assert "form_urlencoded" in option_ids

    def test_body_fields_array_wrapper_exists(self):
        field_map = {f["key"]: f for f in HTTP_REQUEST_INPUT_SCHEMA}
        assert "body_fields" in field_map
        bf = field_map["body_fields"]
        assert bf["outputType"] == "array"
        assert "Wrapper" in str(bf["fieldConfig"]["fieldWrapper"])


class TestStructuredHeaders:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_headers_as_list_of_pairs(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="th1",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "headers": [
                    {"header_name": "Accept", "header_value": "text/plain"},
                    {"header_name": "X-Custom", "header_value": "val"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["headers"]["Accept"] == "text/plain"
        assert call_kwargs.kwargs["headers"]["X-Custom"] == "val"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_headers_empty_list(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="th2",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "headers": [],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        sent_headers = dict(call_kwargs.kwargs["headers"])
        sent_headers.pop("Accept-Encoding", None)  # SA-1940 worker-added
        assert sent_headers == {}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_headers_legacy_dict_format(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="th3",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "headers": {"Accept": "text/plain", "X-Custom": "value"},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["headers"]["Accept"] == "text/plain"
        assert call_kwargs.kwargs["headers"]["X-Custom"] == "value"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_headers_skips_entries_without_name(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="th4",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "headers": [
                    {"header_name": "", "header_value": "ignored"},
                    {"header_name": "Keep", "header_value": "this"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        sent_headers = dict(call_kwargs.kwargs["headers"])
        sent_headers.pop("Accept-Encoding", None)  # SA-1940 worker-added
        assert "Keep" in sent_headers
        assert len(sent_headers) == 1


class TestStructuredQueryParams:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_query_params_as_list_of_pairs(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tq1",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com/search",
                "query_params": [
                    {"param_name": "q", "param_value": "test"},
                    {"param_name": "page", "param_value": "1"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["params"] == {"q": "test", "page": "1"}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_query_params_legacy_dict_format(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tq2",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com/search",
                "query_params": {"q": "test", "page": "1"},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["params"] == {"q": "test", "page": "1"}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_query_params_empty_list(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tq3",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "query_params": [],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        # An empty query-param table must pass ``params=None``, NOT ``{}``: httpx replaces
        # the URL's inline query whenever ``params`` is not None, so ``{}`` would strip any
        # query the URL already carries. (See TestGetRequest regression tests.)
        assert call_kwargs.kwargs["params"] is None


class TestBodyType:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_type_none(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb1",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "none",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] is None

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_type_raw_json(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb2",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "raw_json",
                "body": {"key": "value"},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == json.dumps({"key": "value"}).encode("utf-8")

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_type_raw_text(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb3",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "raw_text",
                "body": "hello world",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == b"hello world"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_type_structured(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb4",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "structured",
                "body_fields": [
                    {"field_name": "name", "field_type": "string", "field_value": "Alice"},
                    {"field_name": "age", "field_type": "number", "field_value": "30"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        sent_body = json.loads(call_kwargs.kwargs["content"].decode("utf-8"))
        assert sent_body == {"name": "Alice", "age": 30}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_type_structured_coerces_types(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb5",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "structured",
                "body_fields": [
                    {"field_name": "count", "field_type": "number", "field_value": "42"},
                    {"field_name": "rate", "field_type": "number", "field_value": "3.14"},
                    {"field_name": "active", "field_type": "boolean", "field_value": "true"},
                    {"field_name": "inactive", "field_type": "boolean", "field_value": "false"},
                    {"field_name": "meta", "field_type": "object", "field_value": '{"k": "v"}'},
                    {"field_name": "tags", "field_type": "array", "field_value": '["a", "b"]'},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        sent_body = json.loads(call_kwargs.kwargs["content"].decode("utf-8"))
        assert sent_body["count"] == 42
        assert sent_body["rate"] == 3.14
        assert sent_body["active"] is True
        assert sent_body["inactive"] is False
        assert sent_body["meta"] == {"k": "v"}
        assert sent_body["tags"] == ["a", "b"]

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_type_form_urlencoded(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb6",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "form_urlencoded",
                "body_fields": [
                    {"field_name": "username", "field_value": "alice"},
                    {"field_name": "password", "field_value": "s3cret"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        content = call_kwargs.kwargs["content"].decode("utf-8")
        assert "username=alice" in content
        assert "password=s3cret" in content
        # Verify Content-Type was set
        assert call_kwargs.kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_type_absent_legacy_fallback(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb7",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body": {"legacy": "data"},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == json.dumps({"legacy": "data"}).encode("utf-8")

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_resolved_body_is_json_string_not_python_repr(self, mock_client_cls, logger, monitor):
        """resolved_body must be a JSON string (true/false) not Python repr (True/False)."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb_resolved",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body": {"isActive": True, "items": [1, 2]},
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        resolved = result.outputs["resolved_body"]
        assert isinstance(resolved, str)
        parsed = json.loads(resolved)
        assert parsed == {"isActive": True, "items": [1, 2]}
        # Must contain JSON booleans, not Python repr
        assert "true" in resolved
        assert "True" not in resolved

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_resolved_body_none_stays_none(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb_none",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "none",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body"] is None

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_resolved_body_raw_text_stays_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tb_text",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "raw_text",
                "body": "plain text",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body"] == "plain text"


class TestPythonReprBodyHandling:
    """Body arriving as Python-repr string must be parsed and sent as valid JSON."""

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_python_repr_body_string_sends_valid_json(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t_repr",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com/api",
                "body_type": "raw_json",
                "body": "{'items':[{'selectionType': 'MATERIAL_TYPE', 'description': '423324', 'isActive': True, 'rules': '423424'}]}",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        sent_bytes = call_kwargs.kwargs["content"]
        parsed = json.loads(sent_bytes)
        assert parsed["items"][0]["isActive"] is True
        assert parsed["items"][0]["selectionType"] == "MATERIAL_TYPE"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_python_repr_body_with_none_sends_valid_json(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t_repr_none",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com/api",
                "body_type": "raw_json",
                "body": "{'key': None, 'active': False}",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        sent_bytes = call_kwargs.kwargs["content"]
        parsed = json.loads(sent_bytes)
        assert parsed["key"] is None
        assert parsed["active"] is False


class TestParseJsonIfStringPythonRepr:
    """_parse_json_if_string must handle Python-repr strings from variable resolver."""

    def test_python_repr_dict_with_booleans(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc._parse_json_if_string("{'isActive': True, 'items': [1, 2]}")
        assert result == {"isActive": True, "items": [1, 2]}

    def test_python_repr_list(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc._parse_json_if_string("[{'name': 'Alice'}, {'name': 'Bob'}]")
        assert result == [{"name": "Alice"}, {"name": "Bob"}]

    def test_python_repr_tuple_rejected(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc._parse_json_if_string("(1, 2, 3)")
        assert result == "(1, 2, 3)"

    def test_valid_json_still_works(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc._parse_json_if_string('{"isActive": true}')
        assert result == {"isActive": True}

    def test_plain_string_returned_as_is(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc._parse_json_if_string("hello world")
        assert result == "hello world"

    def test_non_string_passthrough(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        assert uc._parse_json_if_string(42) == 42
        assert uc._parse_json_if_string({"a": 1}) == {"a": 1}
        assert uc._parse_json_if_string(None) is None


class TestStructuredBodyValidation:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_validation_passes_all_required_present(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="tv1",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "structured",
                "body_fields": [
                    {"field_name": "name", "field_type": "string", "field_value": "Alice", "is_required": True},
                    {"field_name": "age", "field_type": "number", "field_value": "30", "is_required": False},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS

    # is_required and description columns were removed from body_fields;
    # structured body validation now relies on field_name being present.


class TestStringInputConversion:
    """Upstream workflow data arrives as strings — verify proper type coercion."""

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_timeout_as_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ts1",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "timeout_seconds": "60",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["timeout"] == 60

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_follow_redirects_as_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="ts2",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "follow_redirects": "false",
            },
        ))

        mock_client_cls.assert_called_once_with(verify=True, follow_redirects=False)

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_verify_ssl_as_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(ExecuteTaskCommand(
            task_id="ts3",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "verify_ssl": "false",
            },
        ))

        mock_client_cls.assert_called_once_with(verify=False, follow_redirects=True)

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_as_json_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ts4",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "raw_json",
                "body": '{"name": "alice"}',
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == json.dumps({"name": "alice"}).encode("utf-8")

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_headers_as_json_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ts5",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "headers": '[{"header_name": "Accept", "header_value": "text/plain"}]',
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["headers"]["Accept"] == "text/plain"

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_query_params_as_json_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ts6",
            action="http_request",
            inputs={
                "method": "GET",
                "url": "https://example.com",
                "query_params": '[{"param_name": "q", "param_value": "test"}]',
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["params"] == {"q": "test"}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_fields_as_json_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ts7",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body_type": "structured",
                "body_fields": '[{"field_name": "name", "field_type": "string", "field_value": "Bob"}]',
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        sent_body = json.loads(call_kwargs.kwargs["content"].decode("utf-8"))
        assert sent_body == {"name": "Bob"}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_legacy_body_as_json_string(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200)
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="ts8",
            action="http_request",
            inputs={
                "method": "POST",
                "url": "https://example.com",
                "body": '{"legacy": "data"}',
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs.kwargs["content"] == json.dumps({"legacy": "data"}).encode("utf-8")


class TestResponseSizeCap:
    """SA-1940 — the worker STREAMS the response and caps how many bytes it
    reads into RAM, so a large upstream body cannot OOM the worker."""

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_oversize_response_is_capped_and_fails(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        resp = _mock_response(200, headers={"content-type": "application/json"})
        # 10 chunks × 100 bytes = 1000 bytes streamed; cap = 500 → aborted
        # mid-stream, so peak RAM is bounded and the task fails cleanly.
        resp.iter_bytes = MagicMock(return_value=[b"x" * 100 for _ in range(10)])
        resp.iter_raw = MagicMock(return_value=[b"x" * 100 for _ in range(10)])
        mock_client.stream.return_value.__enter__.return_value = resp
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=500)
        result = uc.execute(ExecuteTaskCommand(
            task_id="cap1",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com/huge"},
        ))

        assert result.status == TaskStatus.ERROR
        assert result.outputs["response_too_large"] is True
        assert result.outputs["is_success"] is False
        # Bounded snippet — never the whole payload, never a JSON parse of a
        # truncated body.
        assert len(result.outputs["response_body"]) <= 1000
        assert "exceeded" in (result.error or "")

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_under_cap_response_succeeds(self, mock_client_cls, logger, monitor):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        resp = _mock_response(
            200, headers={"content-type": "application/json"}, json_data={"ok": True},
        )
        mock_client.stream.return_value.__enter__.return_value = resp
        mock_client_cls.return_value = mock_client

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=1_000_000)
        result = uc.execute(ExecuteTaskCommand(
            task_id="cap2",
            action="http_request",
            inputs={"method": "GET", "url": "https://example.com/ok"},
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["response_too_large"] is False
        assert result.outputs["response_body"] == {"ok": True}

    def test_cap_default_and_injection(self, logger, monitor):
        """Cap defaults to the module default and is injectable. The runtime
        source of truth is ``Settings.HTTP_MAX_RESPONSE_BYTES`` (env-tunable),
        injected by ``main.py`` — not read from env inside the use-case."""
        from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
            _DEFAULT_MAX_RESPONSE_BYTES,
        )
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        assert uc._max_response_bytes == _DEFAULT_MAX_RESPONSE_BYTES == 25 * 1024 * 1024
        uc2 = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=777)
        assert uc2._max_response_bytes == 777

    def test_settings_cap_reads_env(self, monkeypatch):
        """Settings.HTTP_MAX_RESPONSE_BYTES (the injected source of truth) is
        env-tunable via Pydantic; main.py feeds it into the use-case."""
        monkeypatch.setenv("HTTP_MAX_RESPONSE_BYTES", "12345")
        from app.layer4_frameworks.config import Settings
        assert Settings().HTTP_MAX_RESPONSE_BYTES == 12345

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _stream(mock_client_cls, raw_chunks, headers):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.headers = headers
        resp.url = "https://example.com/x"
        resp.iter_raw = MagicMock(return_value=list(raw_chunks))
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        cm = client.stream.return_value
        cm.__enter__ = MagicMock(return_value=resp)
        cm.__exit__ = MagicMock(return_value=False)  # fidelity: don't suppress
        mock_client_cls.return_value = client
        return resp

    @staticmethod
    def _get():
        return ExecuteTaskCommand(task_id="cap", action="http_request",
                                  inputs={"method": "GET", "url": "https://example.com/x"})

    # -- boundary -----------------------------------------------------------
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_body_exactly_at_cap_succeeds(self, mock_client_cls, logger, monitor):
        cap = 300
        self._stream(mock_client_cls, [b"y" * cap], {"content-type": "text/plain"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=cap)
        result = uc.execute(self._get())
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["response_too_large"] is False
        assert result.outputs["response_body"] == "y" * cap  # full body, not truncated

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_one_byte_over_cap_is_too_large(self, mock_client_cls, logger, monitor):
        cap = 300
        self._stream(mock_client_cls, [b"y" * (cap + 1)], {"content-type": "text/plain"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=cap)
        result = uc.execute(self._get())
        assert result.outputs["response_too_large"] is True
        assert result.status == TaskStatus.ERROR

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_too_large_metric_tracked(self, mock_client_cls, logger, monitor):
        self._stream(mock_client_cls, [b"x" * 1000], {"content-type": "text/plain"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=100)
        uc.execute(self._get())
        assert ("http_request_response_too_large", 1) in monitor.tracked

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_too_large_snippet_is_raw_string_not_parsed_json(self, mock_client_cls, logger, monitor):
        # A valid-JSON prefix truncated mid-object with content-type application/json
        # must NOT be json.loads-ed — return the raw bounded string.
        body = b'{"a": 1, "big": "' + b"z" * 5000 + b'"}'
        self._stream(mock_client_cls, [body], {"content-type": "application/json"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=200)
        result = uc.execute(self._get())
        assert result.outputs["response_too_large"] is True
        assert isinstance(result.outputs["response_body"], str)  # NOT a dict
        assert result.outputs["response_body"].startswith('{"a": 1')

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_too_large_text_content_type_returns_snippet(self, mock_client_cls, logger, monitor):
        self._stream(mock_client_cls, [b"<html>" + b"x" * 5000], {"content-type": "text/html"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=200)
        result = uc.execute(self._get())
        assert result.outputs["response_too_large"] is True
        assert isinstance(result.outputs["response_body"], str)

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_snippet_bounded_to_1000_chars(self, mock_client_cls, logger, monitor):
        self._stream(mock_client_cls, [b"x" * 6000], {"content-type": "text/plain"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=5000)
        result = uc.execute(self._get())
        assert len(result.outputs["response_body"]) == 1000  # exactly _SNIPPET_CHARS

    # -- compression (SA-1940 HIGH: a compressed body must not blow the cap) --
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_gzip_under_cap_decodes_correctly(self, mock_client_cls, logger, monitor):
        import gzip
        payload = json.dumps({"ok": True, "n": list(range(50))}).encode()
        wire = gzip.compress(payload)
        self._stream(mock_client_cls, [wire],
                     {"content-type": "application/json", "content-encoding": "gzip"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=1_000_000)
        result = uc.execute(self._get())
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["response_too_large"] is False
        assert result.outputs["response_body"] == {"ok": True, "n": list(range(50))}

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_gzip_bomb_is_bounded_and_too_large(self, mock_client_cls, logger, monitor):
        import gzip
        # ~5 MiB of zeros compresses to a few KB, but decodes far past a 1 KiB cap.
        wire = gzip.compress(b"\x00" * (5 * 1024 * 1024))
        cap = 1024
        self._stream(mock_client_cls, [wire],
                     {"content-type": "application/octet-stream", "content-encoding": "gzip"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_response_bytes=cap)
        result = uc.execute(self._get())
        assert result.outputs["response_too_large"] is True  # decode was bounded + flagged
        assert result.status == TaskStatus.ERROR
        # The decoded body handed downstream is bounded to the cap, NOT 5 MiB.
        assert len(result.outputs["response_body"]) <= 1000

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_accept_encoding_limited_to_bounded_codecs(self, mock_client_cls, logger, monitor):
        self._stream(mock_client_cls, [b"ok"], {"content-type": "text/plain"})
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        uc.execute(self._get())
        ae = mock_client_cls.return_value.stream.call_args.kwargs["headers"]["Accept-Encoding"]
        assert "br" not in ae and "zstd" not in ae  # unbounded decoders not offered
        assert "gzip" in ae and "identity" in ae


class TestResolvedBodyDigest:
    """The worker must not echo its whole request body back.

    Origin: run edda4f82-4a09-4587-beb5-8ed615ab8219 (tenant-1, 2026-08-18).
    A body bound to a file_ref resolved to ~12.9 MB of deep JSON; the worker
    echoed it in ``resolved_body``, and the executor could not publish the
    result (12 980 452 B against the 1 048 576 B Event Mesh cap) ->
    RESULT_TOO_LARGE. Worker-side streaming (SA-1944) cannot help: it converts
    list/dict fields, and ``resolved_body`` is a ``str``.

    The field is kept, not deleted: ``resolved_body_bytes == 0`` is the only
    signal that a claim-checked input silently lost its array fields and the
    request went out empty (node_execution_runner.py:63-79).
    """

    @staticmethod
    def _client(mock_client_cls, resp=None):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = resp or _mock_response(200)
        mock_client_cls.return_value = mock_client
        return mock_client

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_small_body_is_echoed_whole_and_marked_untruncated(self, mock_client_cls, logger, monitor):
        """Below the prefix budget nothing changes - the common case is untouched."""
        self._client(mock_client_cls)
        body = {"isActive": True, "items": [1, 2]}

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_small",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com", "body": body},
        ))

        full = json.dumps(body).encode("utf-8")
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body"] == json.dumps(body)
        assert result.outputs["resolved_body_truncated"] is False
        assert result.outputs["resolved_body_bytes"] == len(full)
        assert result.outputs["resolved_body_sha256"] == hashlib.sha256(full).hexdigest()

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_large_body_is_truncated_to_the_prefix_budget(self, mock_client_cls, logger, monitor):
        self._client(mock_client_cls)
        body = {"payload": "x" * 100_000}

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_big",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com", "body": body},
        ))

        full = json.dumps(body).encode("utf-8")
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body_truncated"] is True
        assert len(result.outputs["resolved_body"].encode("utf-8")) <= 4096
        # The FULL size is still reported - nothing is hidden, only not carried.
        assert result.outputs["resolved_body_bytes"] == len(full)
        assert result.outputs["resolved_body_sha256"] == hashlib.sha256(full).hexdigest()

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_whole_output_dict_stays_under_the_broker_cap(self, mock_client_cls, logger, monitor):
        """The regression fence for edda4f82.

        It is not enough that ``resolved_body`` shrinks - the thing the broker
        weighs is the entire outputs dict.
        """
        self._client(mock_client_cls)
        body = [{"deepData": {"product": "P%06d" % i, "blob": "y" * 200}} for i in range(10_000)]

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_fence",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com", "body": body},
        ))

        assert result.status == TaskStatus.SUCCESS
        # the input really is oversized
        assert len(json.dumps(body).encode("utf-8")) > 1024 * 1024
        assert len(json.dumps(result.outputs).encode("utf-8")) < 1024 * 1024

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_empty_body_reports_zero_bytes(self, mock_client_cls, logger, monitor):
        """``resolved_body_bytes == 0`` is the claim-check-lost-my-arrays signal."""
        self._client(mock_client_cls)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_none",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com", "body_type": "none"},
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body"] is None
        assert result.outputs["resolved_body_bytes"] == 0
        assert result.outputs["resolved_body_truncated"] is False

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_large_raw_text_body_is_truncated_too(self, mock_client_cls, logger, monitor):
        """raw_text never goes through json.dumps, so it needs its own guard."""
        self._client(mock_client_cls)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_text",
            action="http_request",
            inputs={
                "method": "POST", "url": "https://example.com",
                "body_type": "raw_text", "body": "z" * 50_000,
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body_truncated"] is True
        assert len(result.outputs["resolved_body"].encode("utf-8")) <= 4096
        assert result.outputs["resolved_body_bytes"] == 50_000

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_truncation_never_splits_a_multibyte_character(self, mock_client_cls, logger, monitor):
        """Cutting on a byte budget must not hand back an undecodable string."""
        self._client(mock_client_cls)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_utf8",
            action="http_request",
            inputs={
                "method": "POST", "url": "https://example.com",
                "body_type": "raw_text", "body": "ạ" * 20_000,
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert isinstance(result.outputs["resolved_body"], str)
        assert result.outputs["resolved_body"].encode("utf-8").decode("utf-8") is not None
        assert len(result.outputs["resolved_body"].encode("utf-8")) <= 4096

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_echo_flag_restores_the_full_body(self, mock_client_cls, logger, monitor):
        """One release of rollback: HTTP_ECHO_RESOLVED_BODY=true is old behaviour."""
        self._client(mock_client_cls)
        body = {"payload": "x" * 100_000}

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, echo_resolved_body=True)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_flag",
            action="http_request",
            inputs={"method": "POST", "url": "https://example.com", "body": body},
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body"] == json.dumps(body)
        assert result.outputs["resolved_body_truncated"] is False
        assert result.outputs["resolved_body_bytes"] == len(json.dumps(body).encode("utf-8"))

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_scalar_body_is_json_not_python_repr(self, mock_client_cls, logger, monitor):
        """A bare ``true`` body must not come back as the Python repr ``True``.

        The dict case is already guarded by
        ``test_resolved_body_is_json_string_not_python_repr``; scalars reach the
        same field by a different branch, so they need their own fence.
        """
        self._client(mock_client_cls)

        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(ExecuteTaskCommand(
            task_id="rb_scalar",
            action="http_request",
            inputs={
                "method": "POST", "url": "https://example.com",
                "body_type": "raw_json", "body": "true",
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["resolved_body"] == "true"
        assert result.outputs["resolved_body"] != "True"
        assert result.outputs["resolved_body_bytes"] == 4
