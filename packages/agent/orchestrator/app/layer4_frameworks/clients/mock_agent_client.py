from app.layer1_domain.entities.permission.permission_context import PermissionContext


class MockAgentClient:
    """Returns placeholder agent responses for development."""

    def list_agents(self) -> list[dict]:
        return [
            {
                "id": "data-import-agent",
                "name": "Data Import Agent",
                "capabilities": ["import", "upload", "ingest"],
                "supported_domains": ["data_import"],
            },
            {
                "id": "data-validation-agent",
                "name": "Data Validation Agent",
                "capabilities": ["validate", "check", "verify"],
                "supported_domains": ["data_validation"],
            },
            {
                "id": "data-transform-agent",
                "name": "Data Transform Agent",
                "capabilities": ["transform", "convert", "map"],
                "supported_domains": ["data_transform"],
            },
            {
                "id": "data-schema-agent",
                "name": "Data Schema Agent",
                "capabilities": ["schema", "model", "define"],
                "supported_domains": ["data_schema"],
            },
        ]

    def dispatch_to_agent(
        self,
        agent_id: str,
        request: dict,
        permission_context: PermissionContext,
    ) -> dict:
        message = request.get("message", "")
        return {
            "message": f"[Mock] Agent '{agent_id}' processed: {message}",
            "agent_id": agent_id,
            "status": "completed",
        }
