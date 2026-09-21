from typing import Any

from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability


class AgentRouter:
    def __init__(self, capabilities: list[AgentCapability]) -> None:
        self._capabilities: dict[str, AgentCapability] = {}
        self.update_capabilities(capabilities)

    def update_capabilities(self, capabilities: list[AgentCapability]) -> None:
        self._capabilities = {cap.agent_type: cap for cap in capabilities}

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        tools = []
        for cap in self._capabilities.values():
            if not cap.enabled:
                continue
            desc = cap.description
            if cap.negative_examples:
                desc += "\n\nDo NOT call this agent when:\n"
                desc += "\n".join(f"- {ex}" for ex in cap.negative_examples)
            tool = {
                "type": "function",
                "function": {
                    "name": f"call_{cap.agent_type.replace('-', '_')}",
                    "description": desc,
                    "parameters": cap.input_schema
                    if cap.input_schema
                    else {"type": "object", "properties": {}},
                },
            }
            tools.append(tool)
        return tools

    def get_capability(self, agent_type: str) -> AgentCapability | None:
        return self._capabilities.get(agent_type)

    def validate_call(self, agent_type: str, params: dict) -> list[str]:
        cap = self._capabilities.get(agent_type)
        if cap is None:
            return [f"Unknown agent_type: {agent_type}"]
        errors = []
        for req in cap.required_parameters:
            if req not in params:
                errors.append(f"Missing required parameter: {req}")
        return errors

    def list_enabled(self) -> list[AgentCapability]:
        return [cap for cap in self._capabilities.values() if cap.enabled]
