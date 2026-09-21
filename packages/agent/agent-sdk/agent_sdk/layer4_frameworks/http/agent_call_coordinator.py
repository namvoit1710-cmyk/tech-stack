from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, TypedDict, cast

import httpx
import jsonschema
from pydantic import ValidationError

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability
from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import InterruptType
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
    ExecuteAgentUseCase,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentInput,
    ResumeAgentUseCase,
)
from agent_sdk.layer2_application.interfaces.agent_endpoint_resolver import (
    IAgentEndpointResolver,
)
from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
    ExecuteAgentOutputPydantic,
)

_RETRYABLE_STATUS_CODES = {502, 503, 504}
_SUB_AGENT_TRANSPORT_ERROR = "SUB_AGENT_TRANSPORT_ERROR"
_AGENT_CALL_CHAIN_LIMIT = "AGENT_CALL_CHAIN_LIMIT"
_COORDINATOR_ERROR_FLAG = "_coordinator_error"
SUB_AGENT_TRANSPORT_ERROR_MESSAGE = "Sub-agent request failed"


class _AgentCallInterruptPayload(TypedDict, total=False):
    type: str
    agent_id: str
    agent_type: str
    input: dict[str, Any] | str
    correlation_id: str | None
    session_id: str | None
    reply_topic: str | None
    reply_queue: str | None
    response_message_type: str | None
    metadata: dict[str, Any]
    context_snapshot: dict[str, Any]


def _coerce_agent_call_payload(
    raw_payload: Any,
) -> _AgentCallInterruptPayload | None:
    if not isinstance(raw_payload, dict):
        return None

    payload_type = raw_payload.get("type")
    agent_id = raw_payload.get("agent_id")
    if (
        payload_type != InterruptType.AGENT_CALL.value
        or not isinstance(agent_id, str)
        or not agent_id
    ):
        return None

    return cast(_AgentCallInterruptPayload, raw_payload)


class AgentCallCoordinator:
    def __init__(
        self,
        endpoint_resolver: IAgentEndpointResolver,
        resume_use_case: ResumeAgentUseCase,
        http_client: httpx.AsyncClient,
        timeout: float = 300.0,
        max_chain_depth: int = 5,
        capabilities: dict[str, AgentCapability] | None = None,
        logger: Any = None,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._resolver = endpoint_resolver
        self._resume_uc = resume_use_case
        self._client = http_client
        self._timeout = timeout
        self._max_chain_depth = max_chain_depth
        self._capabilities = capabilities or {}
        self._logger = logger
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds

    async def execute_with_auto_resume(
        self,
        execute_use_case: ExecuteAgentUseCase,
        request: ExecuteAgentInput,
    ) -> ExecuteAgentOutput:
        depth = 0
        output = await execute_use_case.execute(request)

        while (
            output.interrupted
            and self._is_agent_call(output)
            and depth < self._max_chain_depth
        ):
            agent_call = self._extract_agent_call(output)
            sub_agent_response = await self._call_sub_agent(agent_call)

            if isinstance(sub_agent_response, dict) and sub_agent_response.get(
                _COORDINATOR_ERROR_FLAG
            ):
                return ExecuteAgentOutput(
                    status="error",
                    error=sub_agent_response.get("error"),
                    error_code=sub_agent_response.get("error_code")
                    or _SUB_AGENT_TRANSPORT_ERROR,
                )

            thread_id = agent_call.thread_id
            if not thread_id:
                return ExecuteAgentOutput(
                    status="error",
                    error=SUB_AGENT_TRANSPORT_ERROR_MESSAGE,
                    error_code=_SUB_AGENT_TRANSPORT_ERROR,
                )

            resume_input = ResumeAgentInput(
                thread_id=thread_id,
                resume_value=sub_agent_response,
                interrupt_id=agent_call.interrupt_id,
            )
            resume_output = await self._resume_uc.execute(resume_input)

            output = ExecuteAgentOutput(
                message=resume_output.message,
                status=resume_output.status,
                agent_data=resume_output.agent_data,
                error=resume_output.error,
                error_code=resume_output.error_code,
                correlation_id=resume_output.correlation_id,
                duration_ms=resume_output.duration_ms,
                interrupted=resume_output.interrupted,
                interrupt_payload=resume_output.interrupt_payload,
            )
            depth += 1

        if depth >= self._max_chain_depth and output.interrupted:
            # Only block agent-call interrupts, not HITL interrupts
            if self._is_agent_call(output):
                return ExecuteAgentOutput(
                    error="Max agent call chain depth exceeded",
                    error_code=_AGENT_CALL_CHAIN_LIMIT,
                    status="error",
                )

        return output

    def _is_agent_call(self, output: ExecuteAgentOutput) -> bool:
        if not output.interrupt_payload:
            return False
        return (
            _coerce_agent_call_payload(
                getattr(output.interrupt_payload, "value", output.interrupt_payload)
            )
            is not None
        )

    def _extract_agent_call(self, output: ExecuteAgentOutput) -> AgentCallRequest:
        payload = output.interrupt_payload
        if payload is None:
            raise ValueError("interrupt_payload is required for AGENT_CALL handling")

        value = _coerce_agent_call_payload(getattr(payload, "value", payload))
        if value is None:
            raise ValueError("interrupt_payload must contain an AGENT_CALL payload")

        raw_input = value.get("input", {})
        if isinstance(raw_input, str):
            try:
                input_payload = json.loads(raw_input)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"input_payload must be a dict, got invalid JSON: {exc}"
                ) from exc
        else:
            input_payload = raw_input
        if not isinstance(input_payload, dict):
            raise ValueError(
                f"input_payload must be a dict, got {type(input_payload).__name__}"
            )

        thread_id = payload.thread_id
        if not thread_id:
            raise ValueError("interrupt_payload must contain a thread_id")

        request_message_type = (
            str(
                value.get("request_message_type")
                or value.get("request_type")
                or value.get("message_type")
                or ""
            ).strip()
            or None
        )
        response_message_type = (
            str(
                value.get("response_message_type") or value.get("response_type") or ""
            ).strip()
            or None
        )
        message_overrides = (
            value.get("message_overrides") or value.get("envelope_overrides") or {}
        )

        return replace(
            AgentCallRequest(
                agent_id=value["agent_id"],
                agent_type=str(value.get("agent_type") or ""),
                input_payload=input_payload,
                interrupt_id=payload.interrupt_id or None,
                thread_id=thread_id,
            ),
            correlation_id=value.get("correlation_id"),
            session_id=value.get("session_id"),
            reply_topic=value.get("reply_topic"),
            reply_queue=value.get("reply_queue"),
            metadata=dict(value.get("metadata") or {}),
            context_snapshot=dict(value.get("context_snapshot") or {}),
            request_message_type=request_message_type,
            response_message_type=response_message_type,
            message_overrides=dict(message_overrides)
            if isinstance(message_overrides, dict)
            else {},
        )

    async def _call_sub_agent(self, agent_call: AgentCallRequest) -> Any:
        cap = self._capabilities.get(agent_call.agent_type)
        nested_params = agent_call.input_payload.get("parameters") or {}
        if cap and cap.required_parameters:
            missing = [
                p
                for p in cap.required_parameters
                if p not in agent_call.input_payload and p not in nested_params
            ]
            if missing:
                return {
                    "success": False,
                    "error": f"Missing required parameters: {', '.join(missing)}",
                    "status": "error",
                    "error_code": _SUB_AGENT_TRANSPORT_ERROR,
                    _COORDINATOR_ERROR_FLAG: True,
                }

        if cap and cap.input_schema:
            try:
                validate_instance = {**agent_call.input_payload, **nested_params}
                jsonschema.validate(instance=validate_instance, schema=cap.input_schema)
            except jsonschema.ValidationError as exc:
                return {
                    "success": False,
                    "error": f"Input payload failed schema validation: {exc.message}",
                    "status": "error",
                    "error_code": _SUB_AGENT_TRANSPORT_ERROR,
                    _COORDINATOR_ERROR_FLAG: True,
                }

        timeout = cap.timeout_seconds if cap else self._timeout
        endpoint = await self._resolver.resolve_endpoint(agent_call.agent_id)
        execute_endpoint = endpoint.rstrip("/")
        if not execute_endpoint.endswith("/api/v1/execute"):
            execute_endpoint = f"{execute_endpoint}/api/v1/execute"
        last_error: str = ""

        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(
                    execute_endpoint,
                    json=agent_call.input_payload,
                    timeout=timeout,
                )
                if resp.status_code in _RETRYABLE_STATUS_CODES:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < self._max_retries:
                        await self._backoff(attempt)
                        continue
                    return {
                        "success": False,
                        "error": f"Sub-agent returned {resp.status_code} after {self._max_retries + 1} attempts",
                        "status": "error",
                        "error_code": _SUB_AGENT_TRANSPORT_ERROR,
                        _COORDINATOR_ERROR_FLAG: True,
                    }
                resp.raise_for_status()
                raw = resp.json()
                try:
                    ExecuteAgentOutputPydantic(**raw)
                except (ValidationError, TypeError) as exc:
                    return {
                        "success": False,
                        "error": f"Sub-agent returned invalid response: {exc}",
                        "status": "error",
                        "error_code": _SUB_AGENT_TRANSPORT_ERROR,
                        _COORDINATOR_ERROR_FLAG: True,
                    }
                return raw
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as exc:
                last_error = str(exc)
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                if self._logger:
                    self._logger.error(
                        "Sub-agent transport request failed",
                        endpoint=endpoint,
                        attempt=attempt + 1,
                        exc_info=True,
                    )
                return {
                    "success": False,
                    "error": SUB_AGENT_TRANSPORT_ERROR_MESSAGE,
                    "status": "error",
                    "error_code": _SUB_AGENT_TRANSPORT_ERROR,
                    _COORDINATOR_ERROR_FLAG: True,
                }
            except Exception:
                if self._logger:
                    self._logger.error(
                        "Sub-agent request raised an unexpected exception",
                        endpoint=endpoint,
                        exc_info=True,
                    )
                return {
                    "success": False,
                    "error": SUB_AGENT_TRANSPORT_ERROR_MESSAGE,
                    "status": "error",
                    "error_code": _SUB_AGENT_TRANSPORT_ERROR,
                    _COORDINATOR_ERROR_FLAG: True,
                }

        return {
            "success": False,
            "error": last_error,
            "status": "error",
            "error_code": _SUB_AGENT_TRANSPORT_ERROR,
            _COORDINATOR_ERROR_FLAG: True,
        }

    async def _backoff(self, attempt: int) -> None:
        delay = self._retry_backoff * (2**attempt)
        if self._logger:
            self._logger.info(
                "Retrying sub-agent call in %.1fs (attempt %d/%d)",
                delay,
                attempt + 2,
                self._max_retries + 1,
            )
        await asyncio.sleep(delay)
