from dataclasses import dataclass


@dataclass(kw_only=True)
class WorkerCapability:
    domain: str = ""
    action: str = ""

    def to_dict(self) -> dict:
        return {"domain": self.domain, "action": self.action}

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerCapability":
        return cls(
            domain=data.get("domain", ""),
            action=data.get("action", ""),
        )
