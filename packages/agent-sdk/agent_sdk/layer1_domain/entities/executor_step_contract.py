from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutorStep:
    id: str
    execute_by: str
    next_step_id: str | None = None
    goal: str = ""
    message: str = ""
    uploaded_file_ids: list[str] = field(default_factory=list)
    input_schema: list[str] = field(default_factory=list)
    output_schema: list[str] = field(default_factory=list)
    status: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "execute_by": self.execute_by,
            "next_step_id": self.next_step_id,
            "goal": self.goal,
            "message": self.message,
            "uploaded_file_ids": list(self.uploaded_file_ids),
            "input_schema": list(self.input_schema),
            "output_schema": list(self.output_schema),
            "status": self.status,
        }


@dataclass(frozen=True)
class ExecutorStepEventPayload:
    from_agent: str
    to_agent: str
    payload_type: str
    step: ExecutorStep | None = None
    steps: list[ExecutorStep] = field(default_factory=list)
    batch_id: str | None = None

    def __post_init__(self) -> None:
        if self.payload_type == "step_batch":
            if not self.steps:
                raise ValueError("step_batch payloads require at least one step")
            if self.step is not None:
                raise ValueError("step_batch payloads cannot include a singular step")
            if not self.batch_id:
                raise ValueError("step_batch payloads require a batch_id")
            return
        if self.payload_type == "step":
            if self.step is None:
                raise ValueError("step payloads require a step")
            if self.steps:
                raise ValueError("step payloads cannot include a steps collection")
            return
        raise ValueError(f"unsupported executor step payload type: {self.payload_type}")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "from": self.from_agent,
            "to": self.to_agent,
            "type": self.payload_type,
        }
        if self.batch_id is not None:
            payload["batch_id"] = self.batch_id
        if self.step is not None:
            payload["step"] = self.step.to_payload()
        if self.steps:
            payload["steps"] = [step.to_payload() for step in self.steps]
        return payload
