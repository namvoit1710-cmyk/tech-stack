from __future__ import annotations

import json
import logging


from app.layer1_domain.entities.step_record import StepRecord
from app.layer1_domain.entities.section_definition import fetch_section_definitions, format_section_definitions, SectionDefinition
from app.layer1_domain.entities.enums import AssignType, ContributeResolutionStrategy, ApprovalResolutionStrategy, ProcessExecutionMode, ApprovalProcessAction
logger = logging.getLogger(__name__)


async def build_material_step_records(state: dict, deps: dict) -> dict:
    """Build material workflow step records from a free-form request.

    Args:
        state: The current state containing the message.
        deps: Dependencies required for building step records.
    """
    open_ai_service = deps.get("open_ai_service")
    if open_ai_service is None:
        raise ValueError("open_ai_service is required to build material step records.")
    raw_message_string = state.get("message", "")
    parsed_message = json.loads(raw_message_string)
    sorted_message = sorted(parsed_message, key=lambda x: x['id'])
    template_id = (state.get("parameters") or {}).get("template_id")
    if not template_id:
        raise ValueError("template_id is required to build material step records.")
    section_definitions = await fetch_section_definitions(template_id)
    system_prompt= """
You are a workflow schema generator
=========================================================
BUSINESS WORKFLOW KNOWLEDGE
=========================================================
The typical end-to-end business process follows this order:

  1. CONTRIBUTE  → a user fills in / edits data for a section
                   allowActions includes "EDIT"
                   contributeResolutionStrategy = "ALL_CONTRIBUTED"

  2. APPROVE     → a reviewer checks and approves the contributed data
                   allowActions includes "APPROVE"
                   approvalResolutionStrategy = "ALL_APPROVED"
     REJECT      → a reviewer rejects the contributed data, same level as APPROVE
                   allowActions includes "REJECT"
                   approvalResolutionStrategy = "ALL_APPROVED"
     REWORK      → a reviewer requests corrections, same level as APPROVE
                   allowActions includes "REWORK"
                   approvalResolutionStrategy = "ALL_APPROVED"

  3. ACTIVE      → activates / publishes the CR after all approvals
                   allowActions includes "ACTIVE"

  4. TROUBLESHOOT→ triggered after ACTIVE when issues are found post-activation
                   allowActions includes "TROUBLESHOOT"

Full lifecycle:
  CONTRIBUTE → APPROVE / REJECT / REWORK → ACTIVE → TROUBLESHOOT

  REJECT and REWORK do NOT block ACTIVE — they are parallel outcomes at the approval stage.
  TROUBLESHOOT is always after ACTIVE, never before.

Use this knowledge to:
  ─ Infer stepOrder when the step's position in the flow is implied but not explicit
    contribute steps              → stepOrder 1
    approve/reject/rework steps   → stepOrder 2 (same level, parallel)
    active steps                  → stepOrder 3
    troubleshoot steps            → stepOrder 4

  ─ Infer allowActions when the verb is ambiguous
    "fill", "input", "update", "edit", "contribute"         → "EDIT"
    "check", "validate", "verify", "review", "approve"      → "APPROVE"
    "deny", "decline", "refuse", "not approve"              → "REJECT"
    "request correction", "send back", "return for fix"     → "REWORK"
    "finalize", "publish", "release", "go live", "activate" → "ACTIVE"
    "investigate", "debug", "diagnose", "fix issue"         → "TROUBLESHOOT"

  ─ Infer nextSteps based on the natural flow:
    contribute step    → its related approve/reject/rework step
    approve/reject/
    rework step        → its related active step (if present)
    active step        → its related troubleshoot step (if present); else ""
    troubleshoot step  → "" (terminal)

  ─ REJECT and REWORK share the same stepOrder as APPROVE when they appear together:
    Example: steps at approval stage → same stepOrder even if one is APPROVE and another is REJECT

This is background knowledge only — explicit instructions in the step always take priority.
    """
    
    user_prompt = f"""
Parse the raw input below and return a JSON object with a "records" array — one record per input line.

=========================================================
RAW INPUT (DO NOT MODIFY)
=========================================================
---BEGIN RAW INPUT---
{"\n".join([f"Line {item['id']}: {item['text']}" for item in sorted_message])}
---END RAW INPUT---

=========================================================
ENUM REFERENCE (STRICT — use only these values)
=========================================================
- valid:                         "correct" | "incorrect"
- message:                       "Not related" | "No need section" | "Wrong section" | "Found suitable section" | "Required all data or all fields" | "Default step"
- assignType:                    {enum_values(AssignType)} | null
- allowActions (list or null):   {enum_values(ApprovalProcessAction)}
  ─ TROUBLESHOOT (not TROUBLESHOOTING) is the correct spelling
  ─ Always a JSON array when present: ["EDIT"], ["APPROVE"], ["EDIT", "APPROVE"], etc.
  ─ May contain one or more values — include ALL actions required by the step
  ─ Return null only when no action applies (Not related, Default step with no explicit action)
  ─ Never return [] — use null instead of an empty list
- executionMode:                 {enum_values(ProcessExecutionMode)} | null
  ─ Use SINGLE_TASK for all normal steps; use null only when executionMode must be cleared (Default step, Not related)
- contributeResolutionStrategy:  {enum_values(ContributeResolutionStrategy)} | null
  ─ Always ALL_CONTRIBUTED when the step is a contribute/edit action; otherwise null
- approvalResolutionStrategy:    {enum_values(ApprovalResolutionStrategy)} | null
  ─ Always ALL_APPROVED when the step is an approve/review action; otherwise null

=========================================================
ALLOWED SECTIONS (single source of truth)
=========================================================
{format_section_definitions(section_definitions)}

A section match is valid ONLY when:
  (a) the extracted phrase after the workflow action equals an allowed section name exactly
      (strip surrounding punctuation only; do NOT shorten or prefix-match), OR
  (b) the step mentions at least one allowed field name from the list above
      (map that field to its owning section).

=========================================================
MANDATORY DECISION ORDER — evaluate every step in this order
=========================================================
1. DEFAULT STEP   → step contains a phrase similar in meaning to a default-step phrase, OR mentions "Steward" (case-insensitive, any position including email)
2. ALL DATA       → step says "all data" or "all field"
3. NOT RELATED    → text is not a workflow instruction (greeting, weather, casual statement, etc.)
4. FOUND SECTION  → step can be matched to ≥1 allowed section by exact name or allowed field name
5. WRONG SECTION  → step contains an explicit section-like or data-area phrase that does NOT match
6. NO NEED SECTION→ step is a valid generic workflow step with no section-like phrase at all
7. WRONG SECTION  → fallback

=========================================================
CASE DEFINITIONS
=========================================================

CASE 1 — Not related
  valid = "incorrect" | message = "Not related" | parts = "[]" | skip = false | step_default = false
  assignType = null | assignToId = "" | allowActions = null
  contributeResolutionStrategy = null | approvalResolutionStrategy = null | executionMode = null
  nextSteps: keep if the step still participates in the workflow graph; else ""

CASE 2 — Wrong section
  valid = "incorrect" | message = "Wrong section" | parts = user's phrase  (it did NOT match the allowed list) | skip = false | step_default = false
  Keep assignType, assignToId, allowActions, resolution strategies, executionMode when explicit or inferable.
  nextSteps: keep if the step still participates in the workflow graph; else ""

CASE 3 — Found suitable section
  valid = "correct" | message = "Found suitable section" | parts user's phrase  (it matched the allowed list) | skip = false | step_default = false

CASE 4 — Required all data or all fields
  valid = "correct" | message = "Required all data or all fields" | parts = "[]" | skip = false | step_default = false
  Keep assignType, assignToId, allowActions, resolution strategies, executionMode, nextSteps — same as a normal step.

CASE 5 — Default step
  valid = "correct" | message = "Default step" | parts = "[]" | skip = true | step_default = true
  Steward variant:  assignType = null | assignToId = "STEWARD" | all strategies = null | executionMode = null | nextSteps = ""
  Other variant:    assignType = null | assignToId = "" | all strategies = null | executionMode = null | nextSteps = ""
  allowActions: keep if explicitly stated in the step; else null

CASE 6 — No need section
  valid = "correct" | message = "No need section" | parts = "[]" | skip = false | step_default = false
  Keep all normal workflow fields (assignType, assignToId, allowActions, strategies, executionMode, nextSteps).
  Use ONLY when the step contains NO explicit section-like or data-area phrase.
  If a named phrase is present but invalid → use Wrong section instead.

=========================================================
FIELD EXTRACTION RULES
=========================================================

DESCRIPTION
  Always the exact raw input line — never summarize or modify.

ASSIGN RULE
  email address       → assignType = "USER",   assignToId = <email>
  "username: xxx"     → assignType = "USER",   assignToId = xxx
  Steward default     → assignType = null,      assignToId = "STEWARD"
  automated/system    → assignType = "SYSTEM",  assignToId = "SYSTEM"
    (triggers: automatic, system-triggered, scheduled, background processing, no specific user)

ALLOW ACTIONS RULE
  Modifying / creating / filling / updating / contributing / editing / writing
    → include "EDIT"
  Reviewing / checking / validating / verifying / auditing / inspecting / assessing / evaluating / approving
    → include "APPROVE"
  Activating / activate / active / enabling / enable / switching on
    → include "ACTIVE"
  Investigating / diagnosing / debugging / analyzing issues / resolving incidents / fixing
    → include "TROUBLESHOOT"
  Reviewing / review / checking / check / validating / verifying / auditing /
  inspecting / assessing / evaluating / approving / approve
  → include "APPROVE"
  Remove duplicates. Return only actions required by the step.
  Example: "system will review all data first"
  → assignType = "SYSTEM" | assignToId = "SYSTEM" | allowActions = ["APPROVE"]
  → message = "Required all data or all fields" | valid = "correct"
  "Route to manager to review and contribute" → ["APPROVE", "EDIT"]
  "Send to system to validate and active" → ["APPROVE", "ACTIVE"]

PARTS FORMAT
  PARTS FORMAT
  parts captures the section-like phrase the user explicitly mentioned in the step.
  Extract the phrase that appears after the workflow action verb (contribute, edit, approve, review, etc.).
  Strip only surrounding punctuation. Do NOT modify, shorten, or expand the phrase.

  Extraction examples:
  "contribute Supply Chain data (MRP type, lot size)"  → "Supply Chain"
  "edit General Material Data"                         → "General Material Data"
  "approve Finance-related data"                       → "Finance-related"
  "review and active Finance data (valuation class)"   → "Finance"
  "check all data"                                     → [] (covered by Required all data or all fields)
  "route to manager to approve" (no phrase)            → [] (No need section)

  Shapes:
  "[]"                                                 ← no section phrase mentioned
  "[{{\"section\": \"Supply Chain\"}}]"                  ← one phrase extracted
  "[{{\"section\": \"Supply Chain\"}}, {{\"section\": \"Finance\"}}]"  ← multiple phrases

  Rules:
  ─ Always extract from what the user wrote — never from the allowed list
  ─ Field names in parentheses are clues, not the section name — do not include them in parts
  ─ For Not related, Default step, Required all data or all fields → parts = "[]"
  ─ parts is a JSON array STRING (intentionally wrapped in quotes), NOT a native array

STEP ORDER
  stepOrder represents the execution phase of a step relative to ALL other steps in the input.

  SINGLE STEP INPUT:
    If there is only one step in the entire input, stepOrder = 1 always,
    regardless of whether it is a contribute, approve, or active step.
    Example: "Send to smdg.approver@laidon.com to approve all data" → stepOrder = 1

  MULTI STEP INPUT:
    Assign stepOrder based on the natural business flow and explicit dependencies:
    ─ Steps with no dependency on any other step      → stepOrder = 1
    ─ Steps that depend on stepOrder 1 completing     → stepOrder = 2
    ─ Steps that depend on stepOrder 2 completing     → stepOrder = 3
    ─ Parallel steps (no dependency on each other)    → same stepOrder

    Use BUSINESS WORKFLOW KNOWLEDGE to infer order when not explicit:
      contribute steps              → lower stepOrder
      approve / reject / rework     → after their related contribute steps
      active                        → after all approvals
      troubleshoot                  → after active

    Use explicit keywords to detect dependencies:
      "After contributions, ..."    → this step depends on all contribute steps
      "After all approvals, ..."    → this step depends on all approval steps
      "After X, route to Y"         → Y depends on X

    Parallel example:
      1. contribute Section A  → stepOrder 1
      2. contribute Section B  → stepOrder 1  (parallel, no dependency on each other)
      3. approve Section A     → stepOrder 2
      4. approve Section B     → stepOrder 2  (parallel)
      5. active                → stepOrder 3

NEXT STEPS RULE
  - Contribution step → include its directly related approval step(s) and any "After contributions" step
  - Do NOT cross-link unrelated sections
  - NEVER include steps where skip = true (Default steps)
  - NEVER include final/terminal steps ("After all approvals…")
  - valid = "incorrect" does NOT force nextSteps to be empty
  - Format: comma-separated stepIds, or "" if none
  Example:
    1. Send to a@company.com to contribute Section A          → stepOrder 1
    2. Send to b@company.com to contribute Section B          → stepOrder 1
    3. Route to manager to approve Section A                  → stepOrder 2
    4. Route to manager to approve Section B                  → stepOrder 2
    5. Route to leader to reject Section A                    → stepOrder 2
    6. Route to leader to reject Section B                    → stepOrder 2
    7. Route to reviewer to rework Section A                  → stepOrder 2
    8. Route to reviewer to rework Section B                  → stepOrder 2
    9. After all approvals, route to admin to active          → stepOrder 3
    10. After active, route to supporter to troubleshoot      → stepOrder 4

  Correct nextSteps mapping:
    1  → "3,5,7,9"   (Section A contribute → Section A approve/reject/rework, then active)
    2  → "4,6,8,9"   (Section B contribute → Section B approve/reject/rework, then active)
    3  → "9"         (Section A approve → active)
    4  → "9"         (Section B approve → active)
    5  → "9"         (Section A reject → active)
    6  → "9"         (Section B reject → active)
    7  → "9"         (Section A rework → active)
    8  → "9"         (Section B rework → active)
    9  → "10"        (active → troubleshoot)
    10 → ""          (terminal)

  WRONG:
    1 → "3,4,5,6,7,8,9"  ✗  (Section A must not point to Section B's approve/reject/rework)
    2 → "3,4,5,6,7,8,9"  ✗  (Section B must not point to Section A's approve/reject/rework)
    9 → ""               ✗  (active is not terminal when troubleshoot exists)
    10 → "9"             ✗  (troubleshoot never points back to active)

  Key rules illustrated:
    ─ approve, reject, rework are parallel outcomes at stepOrder 2 — same level, never chained to each other
    ─ All stepOrder 2 steps (regardless of action type) converge into active at stepOrder 3
    ─ active always points to troubleshoot if troubleshoot exists in the input
    ─ troubleshoot is always terminal — nextSteps = ""
    ─ Cross-section linking is always forbidden at the approve/reject/rework level

=========================================================
DEFAULT STEP TRIGGER PHRASES (similar meaning qualifies)
=========================================================
- "After all approvals, proceed to final data check and SAP article creation."
- "If any approver rejects, return CR to the relevant contributor for correction."
- Any step that mentions "Steward" (any casing, including in an email address)

=========================================================
FEW-SHOT EXAMPLE
=========================================================
Note: <AllowedSectionA> and <AllowedFieldA> are placeholders for exact values from the allowed list.

INPUT:
1. Send to a@company.com to edit <AllowedSectionA> (<AllowedFieldA>).
2. After contributions, route to username: manager to review.
3. Send to b@company.com to contribute unknown data.
4. Route to username: approver.lead to approve <AllowedSectionA>.
5. Route to username: Steward to approve any data.

OUTPUT:
{{
  "records": [
    {{
      "description": "1. Send to a@company.com to edit <AllowedSectionA> (<AllowedFieldA>).",
      "stepId": 1,
      "stepOrder": 1,
      "valid": "correct",
      "message": "Found suitable section",
      "parts": "[{{\"section\":\"<AllowedSectionA>\"}}]",
      "assignType": "USER",
      "assignToId": "a@company.com",
      "allowActions": ["EDIT"],
      "contributeResolutionStrategy": "ALL_CONTRIBUTED",
      "approvalResolutionStrategy": null,
      "executionMode": "SINGLE_TASK",
      "nextSteps": "2,4",
      "skip": false,
      "step_default": false
    }},
    {{
      "description": "2. After contributions, route to username: manager to review.",
      "stepId": 2,
      "stepOrder": 2,
      "valid": "correct",
      "message": "No need section",
      "parts": "[]",
      "assignType": "USER",
      "assignToId": "manager",
      "allowActions": ["APPROVE"],
      "contributeResolutionStrategy": null,
      "approvalResolutionStrategy": "ALL_APPROVED",
      "executionMode": "SINGLE_TASK",
      "nextSteps": "",
      "skip": false,
      "step_default": false
    }},
    {{
      "description": "3. Send to b@company.com to contribute unknown data.",
      "stepId": 3,
      "stepOrder": 1,
      "valid": "incorrect",
      "message": "Wrong section",
      "parts": "[]",
      "assignType": "USER",
      "assignToId": "b@company.com",
      "allowActions": ["EDIT"],
      "contributeResolutionStrategy": "ALL_CONTRIBUTED",
      "approvalResolutionStrategy": null,
      "executionMode": "SINGLE_TASK",
      "nextSteps": "2",
      "skip": false,
      "step_default": false
    }},
    {{
      "description": "4. Route to username: approver.lead to approve <AllowedSectionA>.",
      "stepId": 4,
      "stepOrder": 2,
      "valid": "correct",
      "message": "Found suitable section",
      "parts": "[{{\"section\":\"<AllowedSectionA>\"}}]",
      "assignType": "USER",
      "assignToId": "approver.lead",
      "allowActions": ["APPROVE"],
      "contributeResolutionStrategy": null,
      "approvalResolutionStrategy": "ALL_APPROVED",
      "executionMode": "SINGLE_TASK",
      "nextSteps": "",
      "skip": false,
      "step_default": false
    }},
    {{
      "description": "5. Route to username: Steward to approve any data.",
      "stepId": 5,
      "stepOrder": 2,
      "valid": "correct",
      "message": "Default step",
      "parts": "[]",
      "assignType": null,
      "assignToId": "STEWARD",
      "allowActions": ["APPROVE"],
      "contributeResolutionStrategy": null,
      "approvalResolutionStrategy": null,
      "executionMode": null,
      "nextSteps": "",
      "skip": true,
      "step_default": true
    }}
  ]
}}

=========================================================
QUICK REFERENCE — edge cases
=========================================================
| Scenario                                                        | message               | parts                     | skip  |
|-----------------------------------------------------------------|-----------------------|---------------------------|-------|
| "to day rainy"                                                  | Not related           | []                        | false |
| "Send to x@co.com to check all data"                            | Req. all data/fields  | []                        | false |
| "After contributions, route to manager to approve"              | No need section       | []                        | false |
| "route to reviewer to approve General Material Data"            | Wrong section         | [{{"section":"General Material Data"}}] | false |
| "Send to x@co.com to contribute Supply Chain data (MRP type)"  | Wrong section         | [{{"section":"Supply Chain"}}] | false |
| "Send to x@co.com to contribute 1."        (section=1 ok)       | Found suitable section| [{{"section":"1"}}]         | false |
| "Route to Steward to troubleshoot"                              | Default step          | []                        | true  |
| "Send to haha@laidon.com contribute test"                       | Wrong section         | []                        | false |
| "approve Finance-related data"                                  | Wrong section*        | [{{"section":"Finance"}}]   | false |

* Strip trailing qualifiers: "Finance-related data" → "Finance-related ", "Supply Chain data" → "Supply Chain"
* "contribute test" has no recognizable section-like phrase → parts = []
* Wrong section still keeps: assignType, assignToId, allowActions, executionMode when explicit or inferable

Wrong section + metadata rule: even when message = "Wrong section", preserve the assignee,
allowActions, resolution strategy, and executionMode if they are explicit or inferable from the step.

nextSteps cross-section rule:
  Section A contributions → Section A approval only (not Section B approval)
  Example: steps 1(A), 2(B), 3(approveA), 4(approveB), 5(after-all)
    1 → "3,5"   ✓       1 → "3,4,5"  ✗

=========================================================
OUTPUT FORMAT — return ONLY this JSON, no markdown, no explanation
=========================================================
{{
  "records": [
    {{
      "description": string,
      "stepId": number,
      "stepOrder": number,
      "valid": "correct" | "incorrect",
      "message": "Not related" | "No need section" | "Wrong section" | "Found suitable section" | "Required all data or all fields" | "Default step",
      "parts": string,
      "assignType": string | null,
      "assignToId": string,
      "allowActions": list | null,
      "contributeResolutionStrategy": string | null,
      "approvalResolutionStrategy": string | null,
      "executionMode": string | null,
      "nextSteps": string,
      "skip": boolean,
      "step_default": boolean
    }}
  ]
}}
"""
        

    settings = deps.get("app_settings_adapter")
    try:
        response = await open_ai_service.get_chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        records = parse_llm_response(response)
        step_objects = map_to_step_records(records, section_definitions)
        return {
            "formatted_response": {
                "status": "success",
                "message": "Agent run success",
                "agent_type": getattr(settings, "AGENT_TYPE", "unknown"),
                "agent_data": {
                    # "operation": "build_material_step_records",
                    "records": step_objects,
                    # "result": json.dumps(step_objects, ensure_ascii=False),
                },
                "transport_state": "RECEIVED",
            },
            "transport_state": "RECEIVED"
        }
    except Exception as e:
        return {
            "formatted_response": {
                "status": "error",
                "message": f"Agent run fail: {str(e)}",
                "agent_type": getattr(settings, "AGENT_TYPE", "unknown"),
                "agent_data": {
                    # "operation": "build_material_step_records",
                    "error": str(e),
                },
                "transport_state": "RECEIVED",
            },
            "transport_state": "RECEIVED"
        }


def parse_llm_response(response: str):
    """
    Parses LLM response and strictly enforces 'records' key.
    """

    if not response:
        raise ValueError("Empty response from LLM")

    # 1. Parse JSON safely
    try:
        data = json.loads(response)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON from LLM: {e}")

    # 2. Ensure dict
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")

    # 3. Enforce 'records' key
    if "records" not in data:
        raise ValueError("Missing required key: 'records'")

    records = data["records"]

    # 4. Validate structure
    if not isinstance(records, list):
        raise ValueError("'records' must be a list")

    return records

def map_to_step_records(records: list, allowed_sections: list[SectionDefinition]):
    # Normalize enum fields: if value is empty string, None, or null, set to None
    enum_fields = [
        "assignType",
        "allowActions",
        "contributeResolutionStrategy",
        "approvalResolutionStrategy",
        "executionMode",
    ]

    step_objects = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Record at index {index} is not a dict")
        
        # Clean enum fields
        cleaned = record.copy()
        for field in enum_fields:
            val = cleaned.get(field, None)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                cleaned[field] = None

        try:
            step = StepRecord.from_dict(cleaned, allowed_sections)
            step_objects.append(step)

        except Exception as e:
            raise ValueError(f"Failed to map record {index}: {e}")

    return step_objects


def enum_values(enum_cls):
    return ", ".join([e.value for e in enum_cls])

