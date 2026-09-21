class MockWorkflowClient:
    """No-op workflow API client for environments without a workflow base URL."""

    async def get_workflow_by_path(self, routing_path: str, *, tenant_id: str = "system") -> dict | None:
        del routing_path, tenant_id
        return None

    async def get_workflow_by_id(self, workflow_id: str, *, tenant_id: str = "system") -> dict | None:
        del workflow_id, tenant_id
        return None

    async def get_run(self, run_id: str, *, tenant_id: str = "system") -> dict | None:
        del run_id, tenant_id
        return None

    def get_run_sync(self, run_id: str, *, tenant_id: str = "system") -> dict | None:
        del run_id, tenant_id
        return None

    def get_workflow_definition(self, workflow_id: str, *, tenant_id: str = "system") -> dict | None:
        del workflow_id, tenant_id
        return None

    def replay_pending_requests(self, run_id: str, *, tenant_id: str = "system") -> dict | None:
        del run_id, tenant_id
        return None