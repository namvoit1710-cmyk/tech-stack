from typing import Any

from app.layer2_application.interfaces.decision_switch_client_interface import IDecisionSwitchClient


class MockDecisionSwitchClient(IDecisionSwitchClient):
    def dispatch_task(self, task: Any) -> dict:
        print(f"[DecisionSwitch] Dispatching task: {task}")
        return {"status": "dispatched", "worker_id": "mock-worker-001"}
