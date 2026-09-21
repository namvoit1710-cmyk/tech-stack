import re


class MockLLMProvider:
    """Returns placeholder intent classification for development.

    When agents are provided in context, dynamically matches user message
    against each agent's keywords, capabilities, name, and domains.
    Falls back to hardcoded keyword groups when no agents are in context.
    """

    # Legacy hardcoded keyword groups — used when no agents are provided
    _HARDCODED_INTENTS = [
        {
            "keywords": ["import", "upload", "ingest", "load"],
            "domain": "data_import",
            "action": "import",
            "entities": {"source_type": "file"},
            "confidence": 0.85,
        },
        {
            "keywords": ["validate", "check", "verify", "rule"],
            "domain": "data_validation",
            "action": "validate",
            "entities": {},
            "confidence": 0.80,
        },
        {
            "keywords": ["transform", "convert", "map", "format"],
            "domain": "data_transform",
            "action": "transform",
            "entities": {},
            "confidence": 0.80,
        },
        {
            "keywords": ["schema", "model", "define", "structure"],
            "domain": "data_schema",
            "action": "schema",
            "entities": {},
            "confidence": 0.75,
        },
    ]

    def classify_intent(self, message: str, context: dict) -> dict:
        message_lower = message.lower()
        agents = context.get("agents", [])

        # --- Dynamic agent-aware matching ---
        if agents:
            best_match = None
            best_score = 0.0

            for agent in agents:
                score = self._score_agent_match(message_lower, agent)
                if score > best_score:
                    best_score = score
                    best_match = agent

            if best_match and best_score > 0.0:
                domains = best_match.get("supported_domains", [])
                domain = domains[0] if domains else best_match.get("name", "general")
                domain = domain.lower().replace(" ", "_")

                capabilities = best_match.get("capabilities", [])
                action = capabilities[0].split(".")[-1] if capabilities else "handle"
                target_action = action

                confidence = min(0.90, 0.60 + best_score * 0.30)

                return {
                    "domain": domain,
                    "action": action,
                    "target_action": target_action,
                    "entities": {},
                    "confidence": round(confidence, 2),
                    "parameters": self._extract_parameters(message_lower),
                }

        # --- Conversation history fallback for follow-up messages ---
        conversation_history = context.get("conversation_history", [])
        if conversation_history and self._is_likely_followup(message_lower):
            result = self._infer_domain_from_history(
                conversation_history, agents,
            )
            if result:
                result["parameters"] = self._extract_parameters(message_lower)
                return result

        # --- Hardcoded fallback (no agents or no match) ---
        for group in self._HARDCODED_INTENTS:
            if any(kw in message_lower for kw in group["keywords"]):
                action = group["action"]
                return {
                    "domain": group["domain"],
                    "action": action,
                    "target_action": action,
                    "entities": group.get("entities", {}),
                    "confidence": group["confidence"],
                    "parameters": {},
                }

        return {
            "domain": "general",
            "action": "chat",
            "target_action": "chat",
            "entities": {},
            "confidence": 0.50,
            "parameters": {},
        }

    @staticmethod
    def _score_agent_match(message_lower: str, agent: dict) -> float:
        hits = 0.0
        checks = 0

        keywords = agent.get("supported_intents", agent.get("keywords", []))
        if keywords:
            for kw in keywords:
                checks += 1
                if kw.lower() in message_lower:
                    hits += 1.0

        for cap in agent.get("capabilities", []):
            for token in cap.replace(".", " ").split():
                checks += 1
                if token.lower() in message_lower:
                    hits += 0.8

        name = agent.get("name", "")
        for token in name.split():
            tok = token.lower().strip("-_")
            if len(tok) > 2:
                checks += 1
                if tok in message_lower:
                    hits += 0.6

        for domain in agent.get("supported_domains", []):
            for token in domain.replace("_", " ").split():
                tok = token.lower()
                if len(tok) > 2:
                    checks += 1
                    if tok in message_lower:
                        hits += 0.5

        if checks == 0:
            return 0.0
        return min(1.0, hits / max(checks, 1))

    @staticmethod
    def _is_likely_followup(message_lower: str) -> bool:
        continuity_starters = (
            "continue", "also", "now", "then", "and", "next",
            "add", "put", "use", "do", "try", "make",
        )
        followup_phrases = (
            "add to", "the result", "the output", "that result",
            "with that", "from before", "the same", "as well",
            "into the", "to the", "to that", "from that",
        )
        words = message_lower.split()
        if len(words) <= 8:
            if any(message_lower.startswith(w) for w in continuity_starters):
                return True
            if any(phrase in message_lower for phrase in followup_phrases):
                return True
        if any(phrase in message_lower for phrase in followup_phrases):
            return True
        return False

    @classmethod
    def _infer_domain_from_history(
        cls,
        conversation_history: list,
        agents: list,
    ) -> dict | None:
        last_user_msg = None
        for msg in reversed(conversation_history):
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
            if role == "user":
                content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                last_user_msg = content.lower()
                break

        if not last_user_msg:
            return None

        best_match = None
        best_score = 0.0
        for agent in agents:
            score = cls._score_agent_match(last_user_msg, agent)
            if score > best_score:
                best_score = score
                best_match = agent

        if best_match and best_score > 0.0:
            domains = best_match.get("supported_domains", [])
            domain = domains[0] if domains else best_match.get("name", "general")
            domain = domain.lower().replace(" ", "_")

            capabilities = best_match.get("capabilities", [])
            action = capabilities[0].split(".")[-1] if capabilities else "handle"

            return {
                "domain": domain,
                "action": action,
                "target_action": action,
                "entities": {},
                "confidence": 0.75,
            }

        for group in cls._HARDCODED_INTENTS:
            if any(kw in last_user_msg for kw in group["keywords"]):
                action = group["action"]
                return {
                    "domain": group["domain"],
                    "action": action,
                    "target_action": action,
                    "entities": {},
                    "confidence": 0.75,
                }

        return None

    @staticmethod
    def _extract_parameters(message_lower: str) -> dict:
        patterns = [
            r"(?:calculate|compute|eval|evaluate)\s+(.+)",
            r"(?:what is|how much is|what's)\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, message_lower)
            if match:
                expr = match.group(1).strip().rstrip("?.!")
                if expr:
                    return {"expression": expr}
        return {}
