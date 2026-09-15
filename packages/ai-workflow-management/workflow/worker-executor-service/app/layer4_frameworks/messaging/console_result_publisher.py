from typing import Any

from app.layer2_application.interfaces.result_publisher_interface import IResultPublisher


class ConsoleResultPublisher(IResultPublisher):
    def publish_result(self, result: Any) -> None:
        # SA-1941 — mock/dev publisher: never print the full result body (it can
        # carry a large inline worker output). Print a marker only.
        tid = "?"
        if isinstance(result, dict):
            tid = (result.get("payload") or {}).get("task_id") or result.get("task_id") or "?"
        print(f"[ResultPublisher] Publishing result task_id={tid}")
