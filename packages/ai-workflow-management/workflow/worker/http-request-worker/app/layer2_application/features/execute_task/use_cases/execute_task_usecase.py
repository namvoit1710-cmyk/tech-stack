import ast
import base64
import hashlib
import json
import time
import urllib.parse
from typing import Any, Optional

import httpx

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    StreamedCollection,
)
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField,
    ConditionConfig,
    ConditionOperator,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    number_field,
    select_field,
    switch_field,
    array_field,
    text_field,
    textarea_field,
)
from app.layer1_domain.value_objects.http_method import HttpMethod
from app.layer1_domain.value_objects.body_type import BodyType


def _unwrap_multipart(raw: bytes, content_type: str) -> bytes:
    """Return the inner file bytes from a ``multipart/form-data`` download.

    The File Service's ``GET /download`` wraps the stored file in a
    single-part ``multipart/form-data`` envelope (and omits the boundary from
    the ``Content-Type`` header), so a raw ``response.content`` is the MIME
    envelope, not the file. For ``body_type=file`` that would put the boundary
    + part headers into the HTTP request body instead of the file content.

    Detect the envelope (by content-type, falling back to the ``--`` delimiter
    that opens every multipart body) and return the single part's body; the
    boundary is read from the leading delimiter line since the header omits it.
    Anything that doesn't look like multipart passes through unchanged, so this
    is forward-compatible if the service ever returns the raw bytes directly.
    """
    looks_multipart = (
        content_type.lower().startswith("multipart/")
        or raw[:2] == b"--"
    )
    if not looks_multipart or not raw.startswith(b"--"):
        return raw
    nl = raw.find(b"\r\n")
    if nl == -1:
        return raw
    boundary = raw[:nl]  # b"--<boundary>"
    header_end = raw.find(b"\r\n\r\n", nl)  # blank line ends the part headers
    if header_end == -1:
        return raw
    body_start = header_end + 4
    closing = raw.find(b"\r\n" + boundary, body_start)
    return raw[body_start:closing] if closing != -1 else raw[body_start:]


# -- Input Schema (class-based) -----------------------------------------------

# Ceiling for the ``timeout_seconds`` input. ``Settings.HTTP_MAX_TIMEOUT_SECONDS``
# (env-tunable) is the runtime source of truth, injected via ``main.py``; this is
# the fallback for un-wired construction (e.g. tests). Keep in sync.
_DEFAULT_MAX_TIMEOUT_SECONDS = 300


def _timeout_field(max_timeout_seconds: int) -> AutoFormField:
    """The ``timeout_seconds`` form field, with its ceiling from config.

    Built by a factory rather than written inline so the ceiling appears exactly
    once: the FE rule and the runtime guard below must agree, or the form accepts
    a value the worker then rejects.
    """
    return number_field(
        key="timeout_seconds",
        label="Timeout (seconds)",
        placeholder="30",
        default=30,
        rules=[
            ValidationRule(method="min", value=1, message="Minimum timeout is 1 second"),
            ValidationRule(
                method="max",
                value=max_timeout_seconds,
                message=f"Maximum timeout is {max_timeout_seconds} seconds",
            ),
        ],
    )


_INPUT_FIELDS: list[AutoFormField] = [
    # method
    select_field(
        key="method",
        label="HTTP Method",
        options=[(e.value, e.value) for e in HttpMethod],
        placeholder="Select method",
        rules=[ValidationRule(method="required")],
    ),
    # url
    text_field(
        key="url",
        label="URL",
        placeholder="https://api.example.com/resource",
        rules=[
            ValidationRule(method="required"),
            ValidationRule(method="pattern", value="^https?://.+", message="Must be a valid URL"),
        ],
    ),
    # headers (2-column key-value table)
    array_field(
        key="headers",
        label="Headers",
        item_fields=[
            text_field(
                key="header_name",
                label="Header Name",
                placeholder="Content-Type",
                rules=[ValidationRule(method="required")],
            ),
            text_field(
                key="header_value",
                label="Header Value",
                placeholder="application/json",
                rules=[ValidationRule(method="required")],
            ),
        ],
    ),
    # query_params (2-column key-value table)
    array_field(
        key="query_params",
        label="Query Parameters",
        item_fields=[
            text_field(
                key="param_name",
                label="Parameter Name",
                placeholder="key",
                rules=[ValidationRule(method="required")],
            ),
            text_field(
                key="param_value",
                label="Parameter Value",
                placeholder="value",
                rules=[ValidationRule(method="required")],
            ),
        ],
    ),
    # body_type (hidden when method is NOT POST/PUT/PATCH)
    AutoFormField(
        key="body_type",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SELECT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Body Type"},
            control_props={
                "placeholder": "Select body type",
                "options": [
                    {"id": BodyType.NONE.value, "value": BodyType.NONE.value},
                    {"id": BodyType.RAW_JSON.value, "value": BodyType.RAW_JSON.value},
                    {"id": BodyType.RAW_TEXT.value, "value": BodyType.RAW_TEXT.value},
                    {"id": BodyType.STRUCTURED.value, "value": BodyType.STRUCTURED.value},
                    {"id": BodyType.FORM_URLENCODED.value, "value": BodyType.FORM_URLENCODED.value},
                    {"id": BodyType.FILE.value, "value": BodyType.FILE.value},
                ],
            },
            condition=ConditionConfig(
                field_key="method",
                operator=ConditionOperator.IN,
                condition_value=[
                    HttpMethod.GET.value, HttpMethod.DELETE.value,
                    HttpMethod.HEAD.value, HttpMethod.OPTIONS.value,
                ],
            ),
        ),
    ),
    # body (hidden when body_type is NOT raw_json/raw_text)
    AutoFormField(
        key="body",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Request Body"},
            control_props={"placeholder": '{"key": "value"}', "rowsLength": 3},
            condition=ConditionConfig(
                field_key="body_type",
                operator=ConditionOperator.IN,
                condition_value=[
                    "", BodyType.NONE.value,
                    BodyType.STRUCTURED.value, BodyType.FORM_URLENCODED.value,
                    BodyType.FILE.value,
                ],
            ),
        ),
    ),
    # body_fields (hidden when body_type is NOT structured/form_urlencoded)
    AutoFormField(
        key="body_fields",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=FieldWrapper.FRAGMENT,
            wrapper_props={"label": "Body Fields"},
            condition=ConditionConfig(
                field_key="body_type",
                operator=ConditionOperator.IN,
                condition_value=[
                    "", BodyType.NONE.value,
                    BodyType.RAW_JSON.value, BodyType.RAW_TEXT.value,
                    BodyType.FILE.value,
                ],
            ),
        ),
        fields=[
            AutoFormField(
                key="0",
                output_type=OutputType.OBJECT,
                field_config=FieldConfig(field_wrapper=FieldWrapper.FORM_ITEM),
                fields=[
                    AutoFormField(
                        key="field_name",
                        output_type=OutputType.STRING,
                        field_config=FieldConfig(
                            field_control=FieldControl.INPUT,
                            field_wrapper=FieldWrapper.FORM_ITEM,
                            wrapper_props={"label": "Field Name", "required": True},
                            control_props={"placeholder": "field_name"},
                            rules=[ValidationRule(method="required")],
                        ),
                    ),
                    AutoFormField(
                        key="field_type",
                        output_type=OutputType.STRING,
                        field_config=FieldConfig(
                            field_control=FieldControl.SELECT,
                            field_wrapper=FieldWrapper.FORM_ITEM,
                            wrapper_props={"label": "Type"},
                            control_props={
                                "options": [
                                    {"id": "string", "value": "String"},
                                    {"id": "number", "value": "Number"},
                                    {"id": "boolean", "value": "Boolean"},
                                    {"id": "object", "value": "Object"},
                                    {"id": "array", "value": "Array"},
                                ],
                                "value": "string",
                            },
                        ),
                    ),
                    AutoFormField(
                        key="field_value",
                        output_type=OutputType.STRING,
                        field_config=FieldConfig(
                            field_control=FieldControl.INPUT,
                            field_wrapper=FieldWrapper.FORM_ITEM,
                            wrapper_props={"label": "Value"},
                            control_props={"placeholder": "value"},
                        ),
                    ),
                ],
            ),
        ],
    ),
    # body_file (hidden when body_type is NOT file)
    AutoFormField(
        key="body_file",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SINGLE_UPLOAD,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Upload File"},
            condition=ConditionConfig(
                field_key="body_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=BodyType.FILE.value,
            ),
        ),
    ),
    # timeout_seconds
    _timeout_field(_DEFAULT_MAX_TIMEOUT_SECONDS),
    # Authentication is decided entirely by the selected credential
    # (credential_ref → vault-resolved {type, config}). There is deliberately NO separate
    # auth_type field: the credential's own type determines how to authenticate, and its
    # presence determines whether to authenticate at all. A separate auth_type field could
    # disagree with the credential (e.g. auth_type="none" while a credential is selected),
    # which silently dropped the credential and caused a 401 — so it is removed here and the
    # worker-sdk coalesce treats the credential as the source of truth.
    # follow_redirects
    switch_field(key="follow_redirects", label="Follow Redirects", default=True),
    # verify_ssl
    switch_field(key="verify_ssl", label="Verify SSL", default=True),
]

HTTP_REQUEST_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


def build_http_request_input_schema(
    max_timeout_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """The FE form schema with the timeout ceiling taken from deployment config.

    ``HTTP_REQUEST_INPUT_SCHEMA`` is the module-default schema and stays the
    import-time constant existing callers expect; ``main.py`` calls this instead
    so the form the user sees enforces the SAME ceiling the worker enforces.
    """
    cap = int(max_timeout_seconds or _DEFAULT_MAX_TIMEOUT_SECONDS)
    if cap == _DEFAULT_MAX_TIMEOUT_SECONDS:
        return HTTP_REQUEST_INPUT_SCHEMA
    return [
        (_timeout_field(cap) if f.key == "timeout_seconds" else f).to_dict()
        for f in _INPUT_FIELDS
    ]


# -- Output Schema (class-based) ----------------------------------------------

_OUTPUT_FIELDS: list[AutoFormField] = [
    number_field(
        key="status_code",
        label="HTTP Status Code",
        readonly=True,
        rules=[ValidationRule(method="required")],
    ),
    AutoFormField(
        key="response_headers",
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Response Headers"},
            control_props={"readonly": True},
        ),
    ),
    textarea_field(
        key="response_body",
        label="Response Body",
        readonly=True,
    ),
    text_field(
        key="content_type",
        label="Content-Type",
        readonly=True,
    ),
    number_field(
        key="elapsed_ms",
        label="Elapsed Time (ms)",
        readonly=True,
        rules=[ValidationRule(method="required")],
    ),
    switch_field(
        key="is_success",
        label="Is Success",
        readonly=True,
    ),
    switch_field(
        key="response_too_large",
        label="Response Too Large",
        readonly=True,
    ),
    text_field(
        key="final_url",
        label="Final URL",
        readonly=True,
    ),
]

HTTP_REQUEST_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Use Case -----------------------------------------------------------------

# SA-1940 — response-body size cap. ``Settings.HTTP_MAX_RESPONSE_BYTES`` (env-
# tunable) is the runtime source of truth, injected via ``main.py``; this module
# default is the fallback for un-wired construction (e.g. tests). Keep in sync.
_DEFAULT_MAX_RESPONSE_BYTES = 25 * 1024 * 1024  # 25 MiB
_SNIPPET_BYTES = 4096   # wire bytes decoded to build an over-cap error snippet
_SNIPPET_CHARS = 1000   # max chars of that snippet returned to the caller

# How much of the sent body ``resolved_body`` may carry back. Everything above
# this is described (size + sha256) rather than repeated.
#
# WHY THIS EXISTS (measured on tenant-1, run edda4f82-4a09-4587-beb5-8ed615ab8219,
# 2026-08-18): this node had its ``body`` bound to ``{{$input.payload}}``, a
# file_ref whose artifact tree (11 artifacts, 18 378 rows, 1.86 MB of CSV) the
# SDK resolves BEFORE the use-case runs. The request itself was fine — the
# endpoint is a claim-check that answered with ~200 bytes — but the worker then
# echoed the resolved 12.9 MB body back in its outputs, and the executor could
# not publish the result: 12 980 452 B against the 1 048 576 B Event Mesh cap ->
# RESULT_TOO_LARGE, node failed, run failed.
#
# Worker-side streaming (SA-1944) cannot rescue this field: ``should_stream``
# converts ``list``/``dict`` values, and ``resolved_body`` is a ``str`` at any
# size. So the fix is to stop carrying the payload, not to stream it.
#
# The field is TRUNCATED, never dropped. ``resolved_body_bytes == 0`` is the one
# signal that a claim-checked input silently lost its array fields and the
# request went out empty — see the comment at
# ``workflow-control-plane-service/.../node_execution_runner.py`` on
# ``_TASK_INPUT_CLAIM_CHECK_BYTES``. Deleting the field would delete that alarm.
_RESOLVED_BODY_PREFIX_BYTES = 4096
_DEFAULT_ECHO_RESOLVED_BODY = False


class _StreamedJsonBody:
    """Serialise a ``StreamedCollection`` into the request, measuring as it goes.

    Iterating yields the JSON array a chunk at a time, so nothing larger than one
    window is ever resident. The ``resolved_body`` digest (size, sha256, 4 KB
    prefix) is accumulated from the SAME bytes on their way out — it costs
    nothing, and the node still reports exactly what it sent.

    Compact separators on purpose: this is the shape that only exists to be sent,
    and at 23 MB the whitespace is not free.

    Re-iterable, because httpx may retry a request; each pass re-reads the
    collection and resets the measurement rather than doubling it.
    """

    def __init__(self, collection: Any, prefix_bytes: int) -> None:
        self._collection = collection
        self._prefix_bytes = prefix_bytes
        self._reset()

    def _reset(self) -> None:
        self._size = 0
        self._sha = hashlib.sha256()
        self._prefix = b""

    def _emit(self, chunk: bytes) -> bytes:
        self._size += len(chunk)
        self._sha.update(chunk)
        if len(self._prefix) < self._prefix_bytes:
            self._prefix += chunk[: self._prefix_bytes - len(self._prefix)]
        return chunk

    def __iter__(self) -> Any:
        self._reset()
        yield self._emit(b"[")
        first = True
        for obj in self._collection:
            sep = b"" if first else b","
            first = False
            yield self._emit(sep + json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"))
        yield self._emit(b"]")

    def digest(self) -> dict[str, Any]:
        """The same four outputs ``_resolved_body_digest`` produces, measured
        from the stream instead of from a value nobody kept."""
        return {
            "resolved_body": self._prefix.decode("utf-8", errors="ignore"),
            "resolved_body_bytes": self._size,
            "resolved_body_sha256": self._sha.hexdigest(),
            "resolved_body_truncated": self._size > self._prefix_bytes,
        }


class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        file_ref_resolver: Any = None,
        max_response_bytes: int | None = None,
        max_timeout_seconds: int | None = None,
        echo_resolved_body: bool | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self._file_ref_resolver = file_ref_resolver
        # SA-1940 — bound the response body read into RAM (stream + cap) so a
        # large upstream response cannot OOM the worker. Injected from
        # ``Settings.HTTP_MAX_RESPONSE_BYTES`` by ``main.py`` (the runtime source
        # of truth; env-tunable via Pydantic). ``None`` falls back to the module
        # default so tests can construct the use-case without wiring settings.
        self._max_response_bytes = (
            max_response_bytes if max_response_bytes is not None
            else _DEFAULT_MAX_RESPONSE_BYTES
        )
        # Ceiling for the caller's ``timeout_seconds``. Same wiring as above:
        # ``Settings.HTTP_MAX_TIMEOUT_SECONDS`` injected from ``main.py``, module
        # default when un-wired. This is the runtime half of the pair — the form
        # half is ``build_http_request_input_schema``; they must be given the
        # same value or the form accepts what the worker rejects.
        self._max_timeout_seconds = (
            max_timeout_seconds if max_timeout_seconds is not None
            else _DEFAULT_MAX_TIMEOUT_SECONDS
        )
        # Rollback switch for the bounded ``resolved_body`` (see the constant
        # above). ``True`` restores the pre-fix behaviour of echoing the whole
        # body. Injected from ``Settings.HTTP_ECHO_RESOLVED_BODY`` by
        # ``main.py``; keep it for one release, then delete the flag rather than
        # let it calcify.
        self._echo_resolved_body = (
            echo_resolved_body if echo_resolved_body is not None
            else _DEFAULT_ECHO_RESOLVED_BODY
        )

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        self.logger.info(
            "Executing HTTP request task",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("http_request_task_requests", 1)

        start = time.monotonic()

        try:
            # Merge parameters (static node config) with inputs (runtime values).
            # Inputs take precedence so runtime can override static config.
            inputs = {**request.parameters, **request.inputs}
            method = (inputs.get("method") or HttpMethod.GET.value).upper()
            url = inputs.get("url")

            if not url:
                raise ValueError("'url' is required.")

            # -- Validate HTTP method --
            valid_methods = {e.value for e in HttpMethod}
            if method not in valid_methods:
                raise ValueError(
                    f"Invalid HTTP method: '{method}'. Must be one of: {', '.join(e.value for e in HttpMethod)}"
                )

            headers = self._resolve_headers(self._parse_json_if_string(inputs.get("headers")))
            query_params = self._resolve_query_params(self._parse_json_if_string(inputs.get("query_params")))

            body = self._resolve_body(inputs)
            timeout_seconds = self._to_int(inputs.get("timeout_seconds", 30), default=30)
            follow_redirects = self._to_bool(inputs.get("follow_redirects", True), default=True)
            verify_ssl = self._to_bool(inputs.get("verify_ssl", True), default=True)

            # -- Validate timeout range --
            max_timeout = self._max_timeout_seconds
            if not (1 <= timeout_seconds <= max_timeout):
                raise ValueError(
                    f"timeout_seconds must be between 1 and {max_timeout}, "
                    f"got {timeout_seconds}"
                )

            body_type = inputs.get("body_type", "")

            # -- Authentication — the selected credential decides (no separate auth_type) --
            effective_cred = self._resolve_effective_auth(inputs)
            if effective_cred is not None:
                self._apply_effective_auth(headers, effective_cred)

            # -- Build and send request --
            self.logger.info(f"HTTP {method} {url}")

            request_content = self._serialize_body(body, headers, body_type)

            # SA-1940 — only offer encodings we can BOUND-decode (gzip/deflate)
            # or none. httpx otherwise advertises br/zstd too, whose decoders are
            # unbounded per chunk, so a small compressed body could blow past the
            # RAM cap during decompression. A caller-set Accept-Encoding wins.
            headers.setdefault("Accept-Encoding", "gzip, deflate, identity")

            with httpx.Client(verify=verify_ssl, follow_redirects=follow_redirects) as client:
                # SA-1940 — STREAM the response and read at most
                # ``_max_response_bytes`` into RAM. A non-streaming
                # ``client.request`` buffers the whole body before returning, so
                # a large upstream response would OOM the worker; streaming lets
                # us abort the read at the cap, bounding peak RAM.
                with client.stream(
                    method=method,
                    url=url,
                    headers=headers,
                    # httpx REPLACES the URL's inline query with ``params`` whenever
                    # ``params`` is not None — even an empty ``{}`` wipes it. Users often
                    # put the whole query in the URL string and leave the query-param table
                    # empty, so passing ``{}`` silently stripped their query (e.g. a
                    # ".../forecast?latitude=..&longitude=.." call lost its coords and got
                    # an empty 200). Pass None when the table is empty so the URL's own
                    # query survives; only override when the table actually has entries.
                    params=query_params or None,
                    content=request_content,
                    timeout=timeout_seconds,
                ) as response:
                    raw_body, response_too_large = self._read_capped(response)

            duration_ms = (time.monotonic() - start) * 1000
            elapsed_ms = duration_ms

            content_type = response.headers.get("content-type", "")
            response_body = self._parse_response_body(
                raw_body, content_type, response_too_large,
            )

            # A response that blew past the cap could not be fully read, so it
            # is a failure (surfaced explicitly — never a silent truncation).
            is_success = (200 <= response.status_code < 300) and not response_too_large

            self.monitor.track("http_request_task_duration_ms", duration_ms)
            self.monitor.track("http_request_task_success" if is_success else "http_request_task_failure", 1)
            if response_too_large:
                self.monitor.track("http_request_response_too_large", 1)

            outputs = {
                "status_code": response.status_code,
                "response_headers": dict(response.headers),
                "response_body": response_body,
                "content_type": content_type,
                "elapsed_ms": elapsed_ms,
                "is_success": is_success,
                "response_too_large": response_too_large,
                "final_url": str(response.url),
                # When the body streamed, the digest was measured on the way out
                # — there is no value left to measure, and that is the point.
                **(request_content.digest()
                   if isinstance(request_content, _StreamedJsonBody)
                   else self._resolved_body_digest(body)),
            }

            if response_too_large:
                error = (
                    f"HTTP response body exceeded the {self._max_response_bytes}-byte cap "
                    "(HTTP_MAX_RESPONSE_BYTES); streamed and aborted to protect worker memory"
                )
            elif is_success:
                error = None
            else:
                error = self._format_http_error(
                    method, str(response.url), response.status_code, response_body
                )

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS if is_success else TaskStatus.ERROR,
                outputs=outputs,
                error=error,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.error("HTTP request task failed", task_id=request.task_id, error=str(e))
            self.monitor.track("http_request_task_error", 1)

            # Include response info when available (e.g. httpx exceptions
            # that carry a partial response, or ReadTimeout after headers).
            error_outputs: dict[str, Any] = {
                "is_success": False,
                "error_type": type(e).__name__,
            }
            resp = getattr(e, "response", None)
            if resp is not None:
                content_type = resp.headers.get("content-type", "")
                error_outputs["status_code"] = resp.status_code
                error_outputs["response_headers"] = dict(resp.headers)
                error_outputs["content_type"] = content_type
                error_outputs["final_url"] = str(resp.url)
                # Error path: the response came off an httpx exception. If it
                # was already read (e.g. HTTPStatusError) its body is a small,
                # already-buffered 4xx/5xx payload — parse it directly. If it is
                # an unread partial stream, ``.json()``/``.text`` raise and we
                # fall back to empty. No new download happens here, so this path
                # adds no OOM risk beyond what httpx already buffered.
                try:
                    if "application/json" in content_type:
                        error_outputs["response_body"] = resp.json()
                    else:
                        error_outputs["response_body"] = resp.text
                except Exception:
                    error_outputs["response_body"] = ""

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                error=str(e),
                outputs=error_outputs,
                duration_ms=duration_ms,
            )

    def _resolve_effective_auth(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Borrow the node's vault credential by ``credential_ref``.

        The shared coalesce lives in the worker-sdk: the credential resolved from
        ``__credentials`` is the source of truth for auth. HTTP auth is optional — with no
        credential the request is sent unauthenticated (public endpoints), so no
        ``auth_type`` hint and no ``require`` flag are passed. A supplied credential always
        wins, which is what prevents the "credential selected but auth_type=none → 401" bug.
        """
        return resolve_effective_auth(inputs)

    def _apply_effective_auth(
        self, headers: dict[str, str], cred: dict[str, Any]
    ) -> None:
        """Apply a self-describing ``{type, config}`` credential to HTTP headers.

        FAILS CLOSED on a type it cannot apply. This used to fall off the end of
        the if/elif chain and return having set nothing, so a node with a
        credential selected sent an UNAUTHENTICATED request and got back a 401
        that read as the remote's fault. That is the exact failure
        ``resolve_effective_auth`` documents itself as preventing — the invariant
        held at the coalesce layer and was thrown away one layer down.

        It was not hypothetical: every vault adapter built on
        ``JsonBlobAdapterBase`` resolved to ``auth_type="json"``, which matches
        none of these branches. An unknown type now names itself instead, so the
        next unmapped kind surfaces as a clear error rather than a mystery 401.
        """
        cred_type = cred.get("type", "")
        config = cred.get("config", {})

        if cred_type == "bearer":
            headers["Authorization"] = f"Bearer {config.get('token', '')}"
        elif cred_type == "basic":
            encoded = base64.b64encode(
                f"{config.get('username', '')}:{config.get('password', '')}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif cred_type in ("api_key", "api_key_header"):
            header_name = config.get("header", "X-API-Key")
            value = config.get("value", config.get("token", ""))
            headers[header_name] = value
        else:
            raise ValueError(
                f"credential type {cred_type!r} cannot be applied to an HTTP request "
                "(supported: bearer, basic, api_key). The credential was resolved but "
                "no authentication was attached, so the request would have gone out "
                "unauthenticated."
            )

    def _resolve_headers(self, raw_headers: Any) -> dict[str, str]:
        """Convert headers from list-of-pairs or legacy dict format."""
        if isinstance(raw_headers, list):
            result = {}
            for entry in raw_headers:
                name = entry.get("header_name", "")
                if name:
                    result[name] = entry.get("header_value", "")
            return result
        if isinstance(raw_headers, dict):
            return dict(raw_headers)
        return {}

    def _resolve_query_params(self, raw_params: Any) -> dict[str, str]:
        """Convert query params from list-of-pairs or legacy dict format."""
        if isinstance(raw_params, list):
            result = {}
            for entry in raw_params:
                name = entry.get("param_name", "")
                if name:
                    result[name] = entry.get("param_value", "")
            return result
        if isinstance(raw_params, dict):
            return dict(raw_params)
        return {}

    def _resolve_body(self, inputs: dict[str, Any]) -> Any:
        """Resolve body based on body_type."""
        body_type = inputs.get("body_type")
        if body_type is None:
            return self._parse_json_if_string(inputs.get("body"))
        if body_type == BodyType.NONE.value:
            return None
        if body_type == BodyType.RAW_JSON.value:
            return self._parse_json_if_string(inputs.get("body"))
        if body_type == BodyType.RAW_TEXT.value:
            return inputs.get("body")
        if body_type == BodyType.FILE.value:
            return self._download_file(inputs.get("body_file", ""))
        body_fields = self._parse_json_if_string(inputs.get("body_fields", []))
        if not isinstance(body_fields, list):
            body_fields = []
        if body_type == BodyType.STRUCTURED.value:
            return self._build_structured_body(body_fields)
        if body_type == BodyType.FORM_URLENCODED.value:
            return self._build_form_urlencoded_body(body_fields)
        return self._parse_json_if_string(inputs.get("body"))

    def _resolved_body_digest(self, body: Any) -> dict[str, Any]:
        """Describe the body that was sent, without carrying all of it.

        Returns the four ``resolved_body*`` outputs. ``resolved_body`` holds at
        most ``_RESOLVED_BODY_PREFIX_BYTES`` of the serialized body; the full
        size and a sha256 of the full bytes always ride along, so nothing is
        hidden — a caller can still prove what was sent, it just is not repeated
        on the wire. See the constant for the run this comes from.

        Truncation cuts on BYTES (the broker weighs bytes) and then decodes with
        ``errors="ignore"``, so a split multi-byte character is dropped rather
        than handed back as an undecodable string.
        """
        empty = {
            "resolved_body": None,
            "resolved_body_bytes": 0,
            "resolved_body_sha256": "",
            "resolved_body_truncated": False,
        }
        if body is None:
            return empty

        if isinstance(body, (bytes, bytearray)):
            # ``body_type=file`` resolves to raw bytes. Those were never JSON-
            # serializable, so echoing them could only ever break the publish;
            # describe them instead.
            raw = bytes(body)
            return {
                "resolved_body": None,
                "resolved_body_bytes": len(raw),
                "resolved_body_sha256": hashlib.sha256(raw).hexdigest(),
                "resolved_body_truncated": bool(raw),
            }

        # A str passes through as itself (raw_text bodies are already the wire
        # form). EVERYTHING else goes through json.dumps — not just dict/list.
        # ``str(body)`` would be wrong for a scalar: a body of ``true`` parses to
        # Python ``True`` and would come back as ``"True"``, the Python repr this
        # field is explicitly tested not to emit.
        text = body if isinstance(body, str) else json.dumps(body, default=str)
        raw = text.encode("utf-8")
        digest = {
            "resolved_body_bytes": len(raw),
            "resolved_body_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if self._echo_resolved_body or len(raw) <= _RESOLVED_BODY_PREFIX_BYTES:
            return {**digest, "resolved_body": text, "resolved_body_truncated": False}
        return {
            **digest,
            "resolved_body": raw[:_RESOLVED_BODY_PREFIX_BYTES].decode("utf-8", errors="ignore"),
            "resolved_body_truncated": True,
        }

    def _build_structured_body(self, body_fields: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a dict from structured body field definitions."""
        result = {}
        for field in body_fields:
            name = field.get("field_name", "")
            if name:
                raw_value = field.get("field_value", "")
                field_type = field.get("field_type", "string")
                result[name] = self._coerce_value(raw_value, field_type)
        return result

    def _coerce_value(self, raw_value: Any, field_type: str) -> Any:
        """Coerce a raw string value to the declared field type."""
        if raw_value is None or raw_value == "":
            return raw_value
        if field_type == "number":
            try:
                return int(raw_value)
            except (ValueError, TypeError):
                try:
                    return float(raw_value)
                except (ValueError, TypeError):
                    return raw_value
        if field_type == "boolean":
            if isinstance(raw_value, bool):
                return raw_value
            return str(raw_value).lower() in ("true", "1", "yes")
        if field_type in ("object", "array"):
            try:
                return json.loads(raw_value) if isinstance(raw_value, str) else raw_value
            except (json.JSONDecodeError, TypeError):
                return raw_value
        return raw_value

    def _build_form_urlencoded_body(self, body_fields: list[dict[str, Any]]) -> str:
        """Build a URL-encoded string from body field definitions."""
        pairs = []
        for field in body_fields:
            name = field.get("field_name", "")
            if name:
                pairs.append((name, field.get("field_value", "")))
        return urllib.parse.urlencode(pairs)

    def _download_file(self, file_id: str) -> Optional[bytes]:
        """Download raw file bytes from the file service."""
        if not file_id:
            return None
        if not self._file_ref_resolver:
            raise ValueError("file_ref_resolver not configured; cannot download file")
        base_url = self._file_ref_resolver._base_url
        url = f"{base_url}/api/v1/download/{file_id}"
        response = httpx.get(url, timeout=60.0)
        response.raise_for_status()
        # The File Service wraps downloads in a multipart/form-data envelope;
        # unwrap so the request body is the file bytes, not the MIME wrapper.
        return _unwrap_multipart(
            response.content, response.headers.get("content-type", ""),
        )

    def _serialize_body(self, body: Any, headers: dict[str, str], body_type: str = "") -> Any:
        """Serialize the request body — to bytes, or to a STREAM.

        A ``StreamedCollection`` is a file-backed array the SDK handed over
        instead of materialising. Serialising it into a generator is the whole
        point of the exercise: httpx sends the generator chunked, so the
        collection is never a Python object nor a bytes blob, and peak RAM comes
        from the window rather than the payload.
        """
        if isinstance(body, StreamedCollection):
            headers.setdefault("Content-Type", "application/json")
            return _StreamedJsonBody(body, _RESOLVED_BODY_PREFIX_BYTES)
        if body is None:
            return None
        if isinstance(body, bytes):
            headers.setdefault("Content-Type", "application/octet-stream")
            return body
        if body_type == BodyType.FORM_URLENCODED.value:
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            return str(body).encode("utf-8")
        if isinstance(body, (dict, list)):
            headers.setdefault("Content-Type", "application/json")
            return json.dumps(body).encode("utf-8")
        return str(body).encode("utf-8")

    @staticmethod
    def _parse_json_if_string(value: Any) -> Any:
        """If value is a JSON string, parse it; otherwise return as-is.

        Falls back to ast.literal_eval() to handle Python-repr strings
        (single quotes, True/False/None) that arrive from variable resolution.
        Only dict and list results are accepted from literal_eval.
        """
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                pass
            try:
                evaluated = ast.literal_eval(value)
                if isinstance(evaluated, (dict, list)):
                    return evaluated
            except (ValueError, SyntaxError):
                pass
            return value
        return value

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """Convert value to int, returning default on failure."""
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_bool(value: Any, default: bool = True) -> bool:
        """Convert value to bool, handling string representations."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return default

    def _read_capped(self, response: httpx.Response) -> tuple[bytes, bool]:
        """Stream the RAW (wire) body up to the cap, then bound-decode it.

        Returns ``(body_bytes, too_large)``. We read ``iter_raw()`` — the
        UNdecoded wire bytes — not ``iter_bytes()``: httpx's content decoders
        decompress each chunk in full with NO output limit, so a small
        compressed body (a gzip/zstd "bomb") could spike RAM to many times the
        cap during decompression before any length check runs. Capping the wire
        bytes bounds the read, then :meth:`_bounded_decompress` bounds the
        decoded size too, so peak RAM stays ~cap regardless of encoding. The
        request only offers ``gzip, deflate, identity`` (see ``execute``) so the
        encoding is always one we can bound-decode.
        """
        cap = self._max_response_bytes
        wire = bytearray()
        too_large = False
        for chunk in response.iter_raw():
            wire.extend(chunk)
            if len(wire) > cap:
                too_large = True
                del wire[cap:]  # keep only a bounded prefix
                break
        encoding = (response.headers.get("content-encoding") or "").lower().strip()
        if not encoding or encoding == "identity":
            return bytes(wire), too_large
        decoded, decode_overflow = self._bounded_decompress(bytes(wire), encoding, cap)
        return decoded, too_large or decode_overflow

    @staticmethod
    def _bounded_decompress(wire: bytes, encoding: str, cap: int) -> tuple[bytes, bool]:
        """Decompress at most ``cap`` bytes; return ``(bytes, overflowed)``.

        Uses zlib's ``max_length`` so the DECODED size is bounded even for a
        highly compressible body. ``overflowed`` is True when the body would
        exceed the cap (leftover input) or the stream is unexpected/undecodable
        (e.g. an upstream that ignored our ``Accept-Encoding`` and sent br/zstd).
        """
        import zlib

        if encoding in ("gzip", "x-gzip"):
            decomp = zlib.decompressobj(zlib.MAX_WBITS | 16)
        elif encoding == "deflate":
            decomp = zlib.decompressobj()
        else:
            # br / zstd — no stdlib bounded decoder; we never requested them, so
            # refuse rather than risk an unbounded decode.
            return wire[:cap], True
        try:
            out = decomp.decompress(wire, cap + 1)
        except zlib.error:
            return b"", True
        overflow = len(out) > cap or bool(decomp.unconsumed_tail)
        return out[:cap], overflow

    def _parse_response_body(
        self, raw: bytes, content_type: str, too_large: bool = False,
    ) -> Any:
        """Parse the (already size-capped) response body bytes by content-type.

        ``raw`` is bounded by :meth:`_read_capped`. When ``too_large`` the body
        was cut at the cap — do NOT JSON-parse a truncated payload (it would
        raise/mislead); return a bounded UTF-8 snippet (decoded from only the
        first bytes, not the whole cap-sized buffer) so the failure carries
        detail without pretending the data is complete.
        """
        if too_large:
            # Decode only what we return — avoid decoding a 25 MiB buffer to
            # produce a 1 KB snippet.
            return raw[:_SNIPPET_BYTES].decode("utf-8", errors="replace")[:_SNIPPET_CHARS]
        if "application/json" in content_type:
            try:
                return json.loads(raw)
            except Exception:
                return raw.decode("utf-8", errors="replace")
        return raw.decode("utf-8", errors="replace")

    def _format_http_error(self, method: str, url: str, status_code: int, response_body: Any) -> str:
        """Build a descriptive message for a non-2xx HTTP response.

        Includes the method, final URL, status code, and a truncated snippet
        of the response body so the failure surfaces actionable detail (where
        the request went and why it was rejected) instead of a bare status code.
        """
        message = f"HTTP {status_code} for {method} {url}"
        body_snippet = self._stringify_body(response_body)
        if body_snippet:
            message += f" — {body_snippet}"
        return message

    @staticmethod
    def _stringify_body(body: Any, limit: int = 1000) -> str:
        """Render a response body as a single truncated string for messages."""
        if body is None or body == "":
            return ""
        if isinstance(body, (dict, list)):
            try:
                text = json.dumps(body)
            except (TypeError, ValueError):
                text = str(body)
        else:
            text = str(body)
        text = text.strip()
        if len(text) > limit:
            text = text[:limit] + "… (truncated)"
        return text
