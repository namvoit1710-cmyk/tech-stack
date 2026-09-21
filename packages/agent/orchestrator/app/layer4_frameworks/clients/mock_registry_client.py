from app.layer1_domain.entities.agent.scored_agent import ScoredAgent





class MockRegistryClient:
    """Mock adapter for the agent filter API."""

    # SA-892: mock tool registry (name -> stable UUID-form id) so mock-mode stop
    # resolves the workflow-stop capability by name and dispatches a well-formed
    # ``tool.<uuid>`` step (the executor's validator requires the UUID form).
    MOCK_TOOL_IDS = {
        "workflow_stop": "00000000-0000-0000-0000-0000000000ff",
        "workflow_start_run": "00000000-0000-0000-0000-0000000000fe",
    }

    MOCK_AGENTS = [
        {
            "id": "agent-inventory",
            "name": "Inventory Check Agent",
            "kind": "BUSINESS",
            "status": "ACTIVE",
            "is_published": True,
            "description": "Handles inventory validation and lookup tasks.",
            "healthcheck_endpoint": "/health/inventory",
            "invoke_endpoint": "/invoke/inventory",
            "is_alive": True,
            "last_heartbeat": "2026-05-29T06:10:20.537Z",
            "version": "1.0.0",
            "config_type": "runtime",
            "capabilities": ["inventory.lookup"],
            "agents": [],
            "workflows": ["inventory_workflow"],
            "tools": ["inventory-db"],
            "metadata": {},
            "business": "inventory",
            "created_at": "2026-05-29T06:10:20.537Z",
            "updated_at": "2026-05-29T06:10:20.537Z",
        },
        {
            "id": "agent-data-validation",
            "name": "Data Validation Agent",
            "kind": "BUSINESS",
            "status": "ACTIVE",
            "is_published": True,
            "description": "Validates uploaded data and schema consistency.",
            "healthcheck_endpoint": "/health/data-validation",
            "invoke_endpoint": "/invoke/data-validation",
            "is_alive": True,
            "last_heartbeat": "2026-05-29T06:10:20.537Z",
            "version": "1.0.0",
            "config_type": "runtime",
            "capabilities": ["data.validate"],
            "agents": [],
            "workflows": ["validation_workflow"],
            "tools": ["validator"],
            "metadata": {},
            "business": "validation",
            "created_at": "2026-05-29T06:10:20.537Z",
            "updated_at": "2026-05-29T06:10:20.537Z",
        },
        {
            "id": "agent-file-mgmt",
            "name": "File Management Agent",
            "kind": "TECHNICAL",
            "status": "ACTIVE",
            "is_published": False,
            "description": "Manages file storage and retrieval operations.",
            "healthcheck_endpoint": "/health/file-mgmt",
            "invoke_endpoint": "/invoke/file-mgmt",
            "is_alive": True,
            "last_heartbeat": "2026-05-29T06:10:20.537Z",
            "version": "1.0.0",
            "config_type": "runtime",
            "capabilities": ["file.manage"],
            "agents": [],
            "workflows": ["file_workflow"],
            "tools": ["blob-store"],
            "metadata": {},
            "business": "operations",
            "created_at": "2026-05-29T06:10:20.537Z",
            "updated_at": "2026-05-29T06:10:20.537Z",
        },
        {
            "id": "agent-data-import",
            "name": "Data Import Agent",
            "kind": "BUSINESS",
            "status": "INACTIVE",
            "is_published": True,
            "description": "Imports external data files.",
            "healthcheck_endpoint": "/health/data-import",
            "invoke_endpoint": "/invoke/data-import",
            "is_alive": False,
            "last_heartbeat": "2026-05-29T06:10:20.537Z",
            "version": "1.0.0",
            "config_type": "runtime",
            "capabilities": ["data.import"],
            "agents": [],
            "workflows": ["import_workflow"],
            "tools": ["importer"],
            "metadata": {},
            "business": "data_import",
            "created_at": "2026-05-29T06:10:20.537Z",
            "updated_at": "2026-05-29T06:10:20.537Z",
        },
    ]

    async def filter_agents(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        is_published: bool | None = None,
    ) -> list[ScoredAgent]:
        filtered = self.MOCK_AGENTS
        if kind is not None:
            filtered = [agent for agent in filtered if agent["kind"] == kind]
        if status is not None:
            filtered = [agent for agent in filtered if agent["status"] == status]
        if is_published is not None:
            filtered = [agent for agent in filtered if agent["is_published"] is is_published]
        return [ScoredAgent(**agent) for agent in filtered]

    async def get_tool_id_by_name(self, name: str) -> str | None:
        return self.MOCK_TOOL_IDS.get(name)
