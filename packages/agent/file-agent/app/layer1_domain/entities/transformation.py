from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


TransformationBatchType = Literal["column", "row"]


class TransformationBatch(BaseModel):
    type: TransformationBatchType
    items: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_scope(
        cls,
        scope: str | None,
        item: dict[str, Any],
    ) -> TransformationBatch | None:
        if scope == "column":
            return cls(type="column", items=[item])

        if scope == "row":
            return cls(type="row", items=[item])

        return None

    @property
    def payload_key(self) -> Literal["rules", "operations"]:
        return "rules" if self.type == "column" else "operations"

    @property
    def item_count(self) -> int:
        return len(self.items)

    def can_accept(
        self,
        batch_type: TransformationBatchType,
        max_batch_size: int,
    ) -> bool:
        return self.type == batch_type and self.item_count < max_batch_size

    def append_item(self, item: dict[str, Any]) -> None:
        self.items.append(item)

    def to_payload(self, file_path: str, file_format: str) -> dict[str, Any]:
        return {
            "file_path": file_path,
            "file_format": file_format,
            self.payload_key: self.items,
        }


class TransformationApiResult(BaseModel):
    message: str | None = None
    download_url: str | None = None
    file_id: str | None = None
    error: str | None = None
    details: str | None = None

    model_config = ConfigDict(extra="allow")


class TransformationExecutedBatch(BaseModel):
    batch_index: int
    type: TransformationBatchType
    item_count: int
    file_id: str
    download_url: str | None = None
    message: str | None = None

    model_config = ConfigDict(extra="forbid")


class TransformationSuccessResult(BaseModel):
    message: str
    file_id: str
    download_url: str | None = None
    batch_count: int
    batches: list[TransformationExecutedBatch] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class TransformationErrorResult(BaseModel):
    error: str
    details: str | None = None
    failed_batch_index: int | None = None
    batch_type: TransformationBatchType | None = None
    last_successful_file_id: str | None = None
    payload: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


TransformationExecutionResult: TypeAlias = (
    TransformationSuccessResult | TransformationErrorResult
)


class TransformationAgentData(BaseModel):
    agent_type: str
    valid: Literal["correct", "incorrect"]
    message: str
    file_id: str | None = None
    download_url: str | None = None
    batch_count: int | None = None
    error: str | None = None
    details: str | None = None
    failed_batch_index: int | None = None
    batch_type: TransformationBatchType | None = None
    last_successful_file_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class TransformationResponseData(BaseModel):
    message: str
    status: Literal["success", "error"]
    agent_type: str
    agent_data: TransformationAgentData
    transport_state: Literal["RECEIVED"] = "RECEIVED"

    model_config = ConfigDict(extra="forbid")