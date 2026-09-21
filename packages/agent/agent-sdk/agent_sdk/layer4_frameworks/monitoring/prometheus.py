from agent_sdk.layer2_application.interfaces.observability import IMonitor


class PrometheusMonitor(IMonitor):
    def track(self, metric: str, value: float) -> None:
        print(f"[PROMETHEUS] {metric}={value}")
