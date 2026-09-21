from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowApiErrorBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class WorkflowApiSuccessEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["ok"]
    data: dict[str, Any] = Field(default_factory=dict)


class WorkflowApiErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["error"]
    error: WorkflowApiErrorBody


class RuntimeBearerCredentialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


class RuntimeCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bearer"]
    config: RuntimeBearerCredentialConfig


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    input_data: dict[str, Any] = Field(default_factory=dict)

    tenant_id: str = "system"
    correlation_id: str = ""
    callback_topic: str = ""
    predefined_run_id: str | None = None
    skip_validation: bool = False
    execution_mode: str = "auto"
    user_variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    runtime_credentials: dict[str, RuntimeCredential] = Field(default_factory=dict)

    @field_validator("workflow_id")
    @classmethod
    def workflow_id_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("workflow_id is required")
        return value

    @field_validator("predefined_run_id")
    @classmethod
    def predefined_run_id_must_not_be_empty(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        if not value:
            raise ValueError("predefined_run_id must be a non-empty string")

        return value

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude_defaults=True)


class StartRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    workflow_id: str | None = None
    status: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: dict[str, Any] | str | None = None
    output_data: dict[str, Any] | None = None
    execution_mode: str | None = None

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "output_data": self.output_data,
            "execution_mode": self.execution_mode,
        }


class GetDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    node_id: str
    side: str = "input"
    task_id: str | None = None

    @field_validator("run_id", "node_id", "side")
    @classmethod
    def fields_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value

    def to_params(self) -> dict[str, Any]:
        params = {"side": self.side}
        if self.task_id is not None:
            params["task_id"] = self.task_id
        return params


class GetDataResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: dict[str, Any] = Field(default_factory=dict)
    etag: str | None = None

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "etag": self.etag,
        }


class PatchDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    node_id: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    if_match: str | None = None
    task_id: str | None = None

    @field_validator("run_id", "node_id")
    @classmethod
    def fields_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value

    @field_validator("artifacts")
    @classmethod
    def artifacts_must_be_objects(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ValueError("artifacts must be a list")
        for artifact in value:
            if not isinstance(artifact, dict):
                raise ValueError("artifacts must contain only objects")
        return value

    def to_payload(self) -> dict[str, Any]:
        return {"artifacts": self.artifacts}

    def to_params(self) -> dict[str, Any] | None:
        return {"task_id": self.task_id} if self.task_id is not None else None

    def to_headers(self) -> dict[str, str]:
        if self.if_match is None:
            raise ValueError("if_match is required")

        return {"If-Match": self.if_match}


class PatchDataResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: dict[str, Any] = Field(default_factory=dict)

    def to_tool_result(self) -> dict[str, Any]:
        return self.data


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    node_id: str
    task_id: str | None = None

    @field_validator("run_id", "node_id")
    @classmethod
    def fields_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value

    def to_params(self) -> dict[str, Any] | None:
        return {"task_id": self.task_id} if self.task_id is not None else None


class SubmitResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: dict[str, Any] = Field(default_factory=dict)

    def to_tool_result(self) -> dict[str, Any]:
        return self.data


class StopRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str

    @field_validator("run_id")
    @classmethod
    def run_id_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("run_id is required")
        return value


class StopRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    cancelled: bool | None = None
    error: str | None = None
    error_code: str | None = None
    error_details: dict[str, Any] = Field(default_factory=dict)

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "cancelled": self.cancelled,
            "error": self.error,
            "error_code": self.error_code,
            "error_details": self.error_details,
        }
