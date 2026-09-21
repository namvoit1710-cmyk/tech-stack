import json
from typing import Optional

from app.layer1_domain.prompts.build_tasks_prompt import build_orchestration_prompt
from app.layer1_domain.entities.agent.scored_agent import ScoredAgent


class IntentClassifier:
    def build_orchestration_messages(
        self,
        intent: str,
        agents: list[ScoredAgent],
        history: Optional[list[dict[str, str]]] = None,
    ) -> list[dict[str, str]]:
        """Single-call prompt: routing + task breakdown + conversation title in one LLM response."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": build_orchestration_prompt(agents)}
        ]
        if history:
            messages.extend(history)
        messages.append(
            {"role": "user", "content": json.dumps({"message": intent}, indent=2)}
        )
        return messages

    def build_intent(self, message: str, files: list[str]) -> str:
        file_names = [str(file_name).strip() for file_name in files if str(file_name).strip()]
        if not file_names:
            return message

        if len(file_names) == 1:
            file_count_text = "1 file is"
            joined_names = file_names[0]
        elif len(file_names) == 2:
            file_count_text = "2 files are"
            joined_names = f"{file_names[0]} and {file_names[1]}"
        else:
            file_count_text = f"{len(file_names)} files are"
            joined_names = f"{', '.join(file_names[:-1])} and {file_names[-1]}"

        prefix = f"User uploaded {file_count_text} {joined_names}"
        return f"{prefix}. {message}" if message else prefix
