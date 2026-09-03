from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage

from agent_sdk.layer1_domain.entities.context_config import (
    CompactionStrategy,
    ContextBudgetConfig,
    PayloadType,
)

if TYPE_CHECKING:
    from agent_sdk.layer2_application.interfaces.llm_service import ILLMService

logger = logging.getLogger(__name__)


class ContextBudgetManager:
    def __init__(
        self,
        config: ContextBudgetConfig,
        model_name: str = "gpt-4o-mini",
        llm_service: Optional["ILLMService"] = None,
    ):
        self._config = config
        self._encoder = tiktoken.encoding_for_model(model_name)
        self._llm_service = llm_service

    @staticmethod
    def _get_field(msg, field: str, default=""):
        if isinstance(msg, dict):
            return msg.get(field, default)
        return getattr(msg, field, default)

    @staticmethod
    def _get_role(msg) -> str:
        if isinstance(msg, dict):
            return msg.get("role", "unknown")
        return getattr(msg, "type", "unknown")

    def _count_content_tokens(self, content: Any) -> int:
        if isinstance(content, str):
            return len(self._encoder.encode(content))
        if isinstance(content, (list, dict)):
            return len(self._encoder.encode(json.dumps(content)))
        return 0

    def _count_message_tokens(self, msg) -> int:
        content = self._get_field(msg, "content", "")
        if isinstance(content, list):
            text = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        else:
            text = str(content)
        return len(self._encoder.encode(text)) + 4

    def count_tokens(self, messages: list) -> int:
        total = 0
        for msg in messages:
            content = self._get_field(msg, "content", "")
            total += self._count_content_tokens(content)
            total += 4
        return total

    def _truncate_tool_results(self, messages: list) -> list:
        max_tokens = self._config.max_tool_result_tokens
        result: list = []
        for msg in messages:
            if self._get_role(msg) == "tool":
                content = self._get_field(msg, "content", "")
                text = content if isinstance(content, str) else json.dumps(content)
                tokens = self._encoder.encode(text)
                if len(tokens) > max_tokens:
                    truncated_text = self._encoder.decode(tokens[:max_tokens])
                    original_count = len(tokens)
                    truncated_text += (
                        f"\n[truncated — {original_count} → {max_tokens} tokens]"
                    )
                    if isinstance(msg, dict):
                        result.append({**msg, "content": truncated_text})
                    else:
                        result.append(
                            msg.model_copy(update={"content": truncated_text})
                        )
                    continue
            result.append(msg)
        return result

    async def compact(
        self,
        messages: list,
        *,
        payload_types: dict[int, PayloadType] | None = None,
    ) -> tuple[list, list[str]]:
        messages = self._truncate_tool_results(messages)
        total = self.count_tokens(messages)
        threshold = int(
            self._config.max_total_tokens * self._config.compaction_threshold
        )
        warnings: list[str] = []
        if total <= threshold:
            return messages, warnings
        warnings.append(
            f"Context budget: {total}/{self._config.max_total_tokens} tokens"
        )
        strategy = self._config.compaction_strategy
        if strategy == CompactionStrategy.SELECTIVE:
            compacted = self._apply_selective(messages, payload_types)
        elif strategy == CompactionStrategy.HEAD_TAIL:
            compacted = self._apply_head_tail(messages, payload_types)
        elif strategy == CompactionStrategy.TOOL_RESULT_CLEAR:
            compacted = self._apply_tool_result_clearing(messages, payload_types)
        elif strategy == CompactionStrategy.TIERED:
            compacted = await self._apply_tiered(messages, payload_types, total)
        else:
            return messages, warnings
        compacted = self._enforce_hard_limit(compacted, payload_types)
        return compacted, warnings

    def _enforce_hard_limit(
        self,
        messages: list,
        payload_types: dict[int, PayloadType] | None = None,
    ) -> list:
        payload_types = payload_types or {}
        limit = self._config.max_total_tokens
        total = self.count_tokens(messages)
        if total <= limit:
            return messages
        i = 1
        while total > limit and i < len(messages):
            ptype = payload_types.get(i, PayloadType.NARRATIVE)
            if ptype != PayloadType.BUSINESS_DATA:
                dropped_tokens = self._count_message_tokens(messages[i])
                messages = messages[:i] + messages[i + 1 :]
                total -= dropped_tokens
                payload_types = {
                    (k - 1 if k > i else k): v
                    for k, v in payload_types.items()
                    if k != i
                }
            else:
                i += 1
        return messages

    def _apply_selective(
        self,
        messages: list,
        payload_types: dict[int, PayloadType] | None,
    ) -> list:
        N = self._config.max_message_history
        payload_types = payload_types or {}
        result: list = []
        if messages and self._get_role(messages[0]) == "system":
            result.append(messages[0])
            middle = messages[1:-N] if len(messages) > N + 1 else []
            tail = messages[-N:] if len(messages) > N else messages[1:]
        else:
            middle = messages[:-N] if len(messages) > N else []
            tail = messages[-N:] if len(messages) > N else messages

        for i, msg in enumerate(middle):
            orig_idx = i + (1 if result else 0)
            ptype = payload_types.get(orig_idx, PayloadType.NARRATIVE)
            if ptype == PayloadType.BUSINESS_DATA:
                result.append(msg)
            elif self._get_role(msg) == "tool":
                if isinstance(msg, dict):
                    result.append({**msg, "content": "[cleared — old tool result]"})
                else:
                    result.append(
                        msg.model_copy(
                            update={"content": "[cleared — old tool result]"}
                        )
                    )

        result.extend(tail)
        return result

    def _apply_head_tail(
        self,
        messages: list,
        payload_types: dict[int, PayloadType] | None = None,
    ) -> list:
        N = self._config.max_message_history
        payload_types = payload_types or {}
        has_system = messages and self._get_role(messages[0]) == "system"
        head = messages[:1] if has_system else []
        tail_start = max(len(messages) - N, 1 if has_system else 0)
        tail = messages[tail_start:]
        if self._config.preserve_business_payloads:
            middle_start = 1 if has_system else 0
            middle_end = tail_start
            tail_indices = set(range(tail_start, len(messages)))
            for i in range(middle_start, middle_end):
                if i not in tail_indices:
                    ptype = payload_types.get(i, PayloadType.NARRATIVE)
                    if ptype == PayloadType.BUSINESS_DATA:
                        head.append(messages[i])
        return head + tail

    def _apply_tool_result_clearing(
        self,
        messages: list,
        payload_types: dict[int, PayloadType] | None,
    ) -> list:
        N = self._config.max_message_history
        payload_types = payload_types or {}
        result: list = []
        for i, msg in enumerate(messages):
            if i < len(messages) - N and self._get_role(msg) == "tool":
                ptype = payload_types.get(i, PayloadType.NARRATIVE)
                if ptype != PayloadType.BUSINESS_DATA:
                    if isinstance(msg, dict):
                        result.append({**msg, "content": "[cleared]"})
                    else:
                        result.append(msg.model_copy(update={"content": "[cleared]"}))
                    continue
            result.append(msg)
        return result

    async def _summarize_middle(
        self,
        middle_messages: list,
    ) -> str:
        if not self._llm_service:
            raise ValueError("_summarize_middle requires llm_service")
        text_parts: list[str] = []
        for msg in middle_messages:
            role = self._get_role(msg)
            content = self._get_field(msg, "content", "")
            if not isinstance(content, str):
                content = json.dumps(content)
            text_parts.append(f"[{role}] {content}")
        conversation_text = "\n".join(text_parts)
        llm = self._llm_service.get_chat_client()
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Condense the following conversation excerpt into a brief summary "
                        "(max ~800 tokens). Preserve key facts, decisions, and tool results. "
                        "Output plain text, no JSON."
                    )
                ),
                HumanMessage(content=conversation_text),
            ]
        )
        return str(getattr(response, "content", response))

    async def _apply_tiered(
        self,
        messages: list,
        payload_types: dict[int, PayloadType] | None,
        current_tokens: int,
    ) -> list:
        max_tokens = self._config.max_total_tokens
        pressure = current_tokens / max_tokens if max_tokens > 0 else 1.0

        danger = self._config.danger_threshold
        aggressive = self._config.aggressive_threshold

        if pressure >= danger:
            logger.warning(
                "Context budget DANGER: %.0f%% full (%d/%d tokens). "
                "Applying HEAD_TAIL as last resort.",
                pressure * 100,
                current_tokens,
                max_tokens,
            )
            return self._apply_head_tail(messages, payload_types)

        if pressure >= aggressive:
            if self._llm_service:
                return await self._apply_tiered_summarize(messages, payload_types)
            return self._apply_selective(messages, payload_types)

        return self._apply_tool_result_clearing(messages, payload_types)

    async def _apply_tiered_summarize(
        self,
        messages: list,
        payload_types: dict[int, PayloadType] | None,
    ) -> list:
        N = self._config.max_message_history
        payload_types = payload_types or {}
        has_system = messages and self._get_role(messages[0]) == "system"
        head = messages[:1] if has_system else []
        head_len = len(head)

        if len(messages) <= N + head_len:
            return messages

        middle = messages[head_len : len(messages) - N]
        tail = messages[len(messages) - N :]

        business_msgs: list = []
        summarizable: list = []
        for i, msg in enumerate(middle):
            orig_idx = i + head_len
            ptype = payload_types.get(orig_idx, PayloadType.NARRATIVE)
            if ptype == PayloadType.BUSINESS_DATA:
                business_msgs.append(msg)
            else:
                summarizable.append(msg)

        if not summarizable:
            return head + business_msgs + tail

        try:
            summary_text = await self._summarize_middle(summarizable)
            summary_msg = {
                "role": "system",
                "content": f"[Conversation summary]\n{summary_text}",
            }
            return head + business_msgs + [summary_msg] + tail
        except Exception:
            logger.warning(
                "LLM summarization failed, falling back to SELECTIVE.",
                exc_info=True,
            )
            return self._apply_selective(messages, payload_types)
