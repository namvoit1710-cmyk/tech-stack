import json
import logging
from typing import Any
import inspect
import re
from typing import Any, Dict, List

from app.layer1_domain.entities.file_state import ConversationTurn
logger = logging.getLogger(__name__)


def _normalize_conversation_history(history: Any) -> list[ConversationTurn]:
    if not isinstance(history, list):
        return []

    normalized: list[ConversationTurn] = []
    for item in history:
        role = item.role
        message = item.message
        file_id = getattr(item, "file_id", None)

        if isinstance(role, str) and isinstance(message, str):
            normalized.append(ConversationTurn(role=role, message=message, file_id=file_id if isinstance(file_id, str) and file_id else None))

    return normalized


def _conversation_history_prompt(history: list[ConversationTurn]) -> str:
    if not history:
        return "Conversation history: []"

    history_lines = ["Conversation history:"]
    for index, turn in enumerate(history, start=1):
        if turn.file_id:
            history_lines.append(
                f"- turn {index} | role={turn.role} | file_id={turn.file_id} | message={turn.message}"
            )
        else:
            history_lines.append(
                f"- turn {index} | role={turn.role} | message={turn.message}"
            )
    history_lines.append(
        "How to read this log:"
    )
    history_lines.append(
        "- Each turn is chronological; the last user turn is the current request."
    )
    history_lines.append(
        "- file_id identifies the file version attached to that turn."
    )
    history_lines.append(
        "- For revert/turn back requests, choose the previous distinct file_id from the history, not the current one."
    )
    history_lines.append(
        "- Use all prior turns to resolve follow-up requests, including revert, turn back, and references like it, that, this, or previous file changes."
    )
    return "\n".join(history_lines)


def _tool_catalog(deps: dict) -> list[dict[str, Any]]:
    registry = deps.get("tool_registry", {})
    catalog: list[dict[str, Any]] = []
    for tool_name, tool_obj in registry.items():
        catalog.append(
            {
                "name": tool_name,
                "description": getattr(tool_obj, "description", ""),
            }
        )
    return catalog


def _parse_json_payload(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= 0 and end >= start:
        text = text[start : end + 1]
    return json.loads(text)


async def create_plan_node(state: dict, deps: dict) -> dict:
    message = state.get("message", "")
    parameters = state.get("parameters", {})
    file_id = parameters.get("file_id", "")
    run_id = parameters.get("run_id", "")
    node_id = parameters.get("node_id", "")
    conversation_id = state.get("conversation_id", "")
    conversation_history = _normalize_conversation_history(state.get("conversation_history", []))
    conversation_history.append(ConversationTurn(role="user", message=message, file_id=file_id or None))
    tool_catalog = _tool_catalog(deps)
    conversation_history_prompt = _conversation_history_prompt(conversation_history)
    prompt = (
        "You are the planning node for a tool-using agent. "
        "Create a JSON execution plan only. "
        "Select only tools from the provided catalog. "
        f"Use only the available tools: {json.dumps(tool_catalog, ensure_ascii=False)}. "

        "CONTEXT SUMMARY: "
        f"conversation_id={conversation_id or 'unknown'}; "
        f"current_file_id={file_id or 'none'}; "
        f"current_run_id={run_id or 'none'}; "
        f"current_node_id={node_id or 'none'}; "

        "ROW POSITION RULES (IMPORTANT): "
        "- Treat user-supplied row position references as 1-based positions counted from the first data row after the header. "
        "- Do not treat those references as zero-based indexes. "
        "- Exclude the header row from the count. "
        "- Apply this interpretation consistently whenever a step depends on row positions. "

        "ALTERNATIVE INTENT RULE (IMPORTANT): "
        "- If the user request presents alternatives or mutually exclusive actions, choose one best-fit intent and plan only that intent. "
        "- Do not combine multiple alternative branches into the same execution plan. "
        "- If the request is ambiguous, prefer the interpretation most strongly supported by the surrounding words and the latest user turn. "

        "PLANNING OBJECTIVE: "
        "Break the user request into an optimal sequence of tool executions while minimizing the number of steps. "

        "TOOL SELECTION RULES: "
        "- You may use one or more tools depending on the user request. "
        "- Each step must correspond to exactly one tool invocation. "

        "BATCHING RULES (IMPORTANT): "
        "- Check tool metadata (description and capabilities) to determine if a tool supports multiple operations in a single call. "
        "- If a tool supports batching, group all compatible operations into a single step. "
        "- If a tool does NOT support batching, split operations into separate steps. "
        "- Never assume batching capability unless explicitly indicated in tool metadata. "

        "DATA FLOW RULES (CRITICAL): "
        "- Carefully read each tool's description to understand its output behavior. "
        "- If a tool produces a new or updated resource (e.g., a new file_id), treat it as the latest state. "
        "- That output MUST be used in subsequent steps when further operations depend on the updated data. "
        "- Do NOT assume all tools behave the same—some update in place, others create new outputs. "
        "- Always propagate the most recent valid state when chaining steps. "

        "REFERENCE RULES: "
        "- When a step depends on the output of a previous step, reference it using: $step_<order>.file_id "
        "- Example: $step_1.file_id "
        "- Only use references when the previous step produces a new or updated file_id. "
        "- If no new file is produced, reuse the existing input as appropriate. "

        "EXECUTION RULES: "
        "- Steps must be in logical execution order. "
        "- Later steps may depend on outputs of earlier steps. "
        "- Prefer fewer steps when possible without changing correctness. "

        "VALIDATION RULES (STRICT): "
        "- If a tool produces a new file_id or updated resource, subsequent steps MUST use that output when required. "
        "- A plan is INVALID if it ignores a required intermediate output and incorrectly reuses an outdated input. "

        "RETURN FORMAT (STRICT): "
        "{\"goal\": string, \"steps\": [{\"order\": number, \"tool\": string, \"input\": object, \"reason\": string}]}. "

        "CONSTRAINTS: "
        "- Output ONLY valid JSON. "
        "- Do NOT include markdown, commentary, or explanations. "
        "- Do NOT include extra fields outside the schema. "

        f"\n\n{conversation_history_prompt}"

        f"\n\nParameters: {json.dumps(parameters, ensure_ascii=False)}"
    )
    
    open_ai_service = deps.get("open_ai_service")
    if open_ai_service is None:
        raise ValueError("open_ai_service is required to build a tool plan.")

    if tool_catalog:
        user_prompt = message
        try:
            response = await open_ai_service.get_chat_completion(prompt, user_prompt)
            parsed = _parse_json_payload(response)
            if isinstance(parsed, dict) and parsed.get("steps"):
                return {
                    "tool_catalog": tool_catalog,
                    "execution_plan": parsed,
                    "conversation_history": conversation_history,
                }
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise ValueError("Failed to create execution plan from model response.") from exc

    raise ValueError("No tools available for planning.")

async def execute_plan_node(state: dict, deps: dict) -> dict:
    plan = state.get("execution_plan") or {}
    registry = deps.get("tool_registry", {})
    conversation_history = _normalize_conversation_history(state.get("conversation_history", []))

    # 1. Validate plan BEFORE execution
    validate_plan(plan, registry)
    steps = sorted(plan.get("steps", []), key=lambda x: x.get("order", 0))

    # 2. Execute steps
    summary = await execute_steps(steps, registry, plan)
    logger.info("execute_plan_node summary: %s", summary)

    # 3. Friendly message generation
    open_ai_service = deps.get("open_ai_service")
    friendly_message = await generate_friendly_message(summary, open_ai_service)

    setting = deps.get("app_settings")
    latest_file_id = ""
    has_error = any(
        isinstance(result, dict) and result.get("status") == "error"
        for result in summary.get("results", [])
    )
    for result in reversed(summary.get("results", [])):
        if isinstance(result, dict) and result.get("result"):
            result_data = result["result"]
            if isinstance(result_data, dict) and result_data.get("file_id"):
                latest_file_id = result_data["file_id"]
                break

    response_status = "error" if has_error else "success"
    response_content = friendly_message if not has_error else "Your request could not be completed due to a temporary issue. Please try again."
    conversation_history.append(ConversationTurn(role="system", message=response_content, file_id=latest_file_id or None))
    logger.info("summary response: %s", summary)
    return {
        "formatted_response": {
            "status": response_status,
            "content": response_content,
            "agent_type": setting.AGENT_TYPE if setting else "unknown",
            "agent_data": {
                "file_id": latest_file_id,
                "summary": summary,
            },
        },
        "conversation_history": conversation_history,
    }

async def _run_tool(tool_obj, tool_input):
    if hasattr(tool_obj, "ainvoke"):
        return await tool_obj.ainvoke(tool_input)

    if hasattr(tool_obj, "invoke"):
        result = tool_obj.invoke(tool_input)
        return await result if inspect.isawaitable(result) else result

    raise TypeError(f"Unsupported tool: {type(tool_obj)}")

REFERENCE_PATTERN = re.compile(r"\$step_(\d+)\.(\w+)")


def _extract_references(obj: Any) -> List[tuple[int, str]]:
    refs = []

    if isinstance(obj, dict):
        for v in obj.values():
            refs.extend(_extract_references(v))
    elif isinstance(obj, list):
        for v in obj:
            refs.extend(_extract_references(v))
    elif isinstance(obj, str):
        match = REFERENCE_PATTERN.match(obj)
        if match:
            refs.append((int(match.group(1)), match.group(2)))

    return refs


def validate_plan(plan: Dict, registry: Dict) -> None:
    try:
        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan: missing steps")

        steps = sorted(plan["steps"], key=lambda s: s.get("order", 0))
        seen_orders = set()

        for step in steps:
            order = step.get("order")
            tool = step.get("tool")
            input_data = step.get("input", {})

            # Order validation
            if not isinstance(order, int):
                raise ValueError(f"Invalid step order: {order}")
            if order in seen_orders:
                raise ValueError(f"Duplicate step order: {order}")
            seen_orders.add(order)

            # Tool validation
            if tool not in registry:
                raise ValueError(f"Unknown tool: {tool}")

            # Reference validation
            refs = _extract_references(input_data)
            for ref_step, field in refs:
                if ref_step >= order:
                    raise ValueError(
                        f"Invalid reference in step {order}: "
                        f"cannot reference future step {ref_step}"
                    )

        # Optional: enforce at least 1 step
        if not steps:
            raise ValueError("Plan has no steps")
    except Exception as e:
        raise ValueError(f"Validation failed: {str(e)}")


async def execute_steps(steps: list, registry: Dict, plan: Dict) -> dict:
    execution_results = []
    step_outputs: Dict[int, Dict] = {}

    for step in steps:
        order = step.get("order")
        tool_name = step.get("tool")
        raw_input = step.get("input", {}) or {}
        tool_obj = registry.get(tool_name)

        try:
            # Resolve references
            resolved_input = await resolve_references(raw_input, step_outputs)

            # Execute tool
            result = await _run_tool(tool_obj, resolved_input)

            # Store output for chaining
            step_outputs[order] = result or {}

            execution_results.append({
                "order": order,
                "tool": tool_name,
                "input": resolved_input,
                "result": result,
                "status": "success"
            })

        except Exception as e:
            execution_results.append({
                "order": order,
                "tool": tool_name,
                "input": raw_input,
                "status": "error",
                "reason": str(e)
            })

            # Optional: stop execution on failure
            break

    summary = {
        "status": "completed",
        "goal": plan.get("goal", ""),
        "results": execution_results,
    }

    return summary

async def resolve_references(obj: Any, step_outputs: Dict[int, Dict]) -> Any:
    if isinstance(obj, dict):
        return {k: await resolve_references(v, step_outputs) for k, v in obj.items()}

    if isinstance(obj, list):
        return [await resolve_references(v, step_outputs) for v in obj]

    if isinstance(obj, str):
        match = REFERENCE_PATTERN.match(obj)
        if match:
            step_num = int(match.group(1))
            field = match.group(2)

            if step_num not in step_outputs:
                raise ValueError(f"Missing step output: step {step_num}")

            value = step_outputs[step_num].get(field)

            if value is None:
                raise ValueError(
                    f"Field '{field}' not found in step {step_num} output"
                )

            return value

    return obj

async def generate_friendly_message(summary: dict, open_ai_service) -> str:
    try:
        friendly_prompt = (
            "You are a helpful assistant rewriting technical execution summaries into simple, friendly, human-readable messages.\n"
            "Make it sound natural, like you're explaining it to a non-technical person.\n"
            "Keep it clear, warm, and easy to understand — but do NOT add opinions or extra facts.\n\n"
            "Mention download links if there are any, but do NOT mention file IDs or technical details.\n"
            "Return ONLY valid JSON in this exact format:\n"
            "{\"message\": \"...\"}\n"
            "Do not include any extra text outside JSON.\n\n"
            f"Here is the execution summary:\n{json.dumps(summary, ensure_ascii=False)}"
        )
        summary_response = await open_ai_service.get_chat_completion(friendly_prompt, "")
        return _parse_json_payload(summary_response).get("message", "Execution completed.")
    except Exception as e:
        print(f"Failed to generate friendly message: {str(e)}")
        return "Execution completed."