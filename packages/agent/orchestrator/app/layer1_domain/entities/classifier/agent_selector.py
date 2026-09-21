from app.layer1_domain.entities.agent.scored_agent import ScoredAgent


class AgentSelector:
    async def select_agent(
        self,
        agents: list[ScoredAgent],
    ) -> ScoredAgent | None:
        eligible_agents = [agent for agent in agents if agent.is_published and agent.is_alive]
        selected_agent = eligible_agents[0] if eligible_agents else (agents[0] if agents else None)
        return selected_agent