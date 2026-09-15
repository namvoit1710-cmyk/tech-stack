"""Multi-type node registry for the jira-worker.

Every Jira operation is registered as its OWN node type via worker-sdk
``node_types=[...]`` (the SA-1078 / SA-1549 multi-type pattern). Twenty-two nodes,
one shared ``JiraClient``, one shared vault credential (``jira-api-token`` → the
runtime ``{type:"json", config:{domain,email,apiToken}}`` blob).

Handler contract (worker-sdk ``routes.py``): a node handler is
``(inputs, parameters) -> dict`` and **raises on failure** — the multi-type route
maps a raised exception to ``TaskStatus.ERROR`` and a normal return to
``TaskStatus.SUCCESS`` ("success"/"error", SA-1078). So validation problems raise
``ValueError("VALIDATION_ERROR: ...")`` and API problems let ``JiraError`` (whose
message already carries the Jira status + detail) propagate; success returns a dict.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Dict, List, Optional

from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    number_field,
    switch_field,
    text_field,
    textarea_field,
)
from worker_sdk import NodeKind
from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth

from app.functions import (
    list_assignable_users_function,
    list_issue_types_function,
    list_projects_function,
    list_transitions_function,
    test_connection_function,
)
from app.layer2_application.services.jira_client import JiraClient, text_to_adf
from app.layer4_frameworks.config import settings

_TAGS = ["jira", "atlassian", "issue", "ticket"]


# -- input / value helpers ----------------------------------------------------


def _req() -> List[ValidationRule]:
    return [ValidationRule(method="required")]


def _merge(inputs: Optional[Dict[str, Any]], parameters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge parameters + inputs (inputs win), matching the fleet convention."""
    return {**(parameters or {}), **(inputs or {})}


def _require(merged: Dict[str, Any], key: str, label: Optional[str] = None) -> Any:
    value = merged.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"VALIDATION_ERROR: '{label or key}' is required")
    return value.strip() if isinstance(value, str) else value


def _opt(merged: Dict[str, Any], key: str) -> Optional[str]:
    value = merged.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _csv(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"VALIDATION_ERROR: expected a number, got {value!r}") from None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _parse_json_object(value: Any, label: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (ValueError, TypeError):
        raise ValueError(f"VALIDATION_ERROR: '{label}' must be valid JSON") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"VALIDATION_ERROR: '{label}' must be a JSON object")
    return parsed


def _client_from(merged: Dict[str, Any]) -> JiraClient:
    """Resolve the vault credential (fail closed) and build a JiraClient."""
    cred = resolve_effective_auth(merged, require=True)
    return JiraClient.from_credential(cred)


def _ok(**outputs: Any) -> Dict[str, Any]:
    return {"success": True, "error": None, "error_code": None, **outputs}


# -- output-schema field helpers ---------------------------------------------


def _o_bool(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


def _o_text(key: str, label: str) -> AutoFormField:
    return text_field(key, label, readonly=True)


def _o_number(key: str, label: str) -> AutoFormField:
    return number_field(key, label, readonly=True)


def _o_array(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


def _o_object(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


def _outputs(*fields: AutoFormField) -> List[AutoFormField]:
    return [_o_bool("success", "Success"), *fields, _o_text("error", "Error"), _o_text("error_code", "Error Code")]


def _in_issue_key() -> AutoFormField:
    return text_field("issue_key", "Issue Key", placeholder="e.g. SA-123", rules=_req())


def _node(
    worker_type: str,
    name: str,
    description: str,
    icon: str,
    inputs: List[AutoFormField],
    outputs: List[AutoFormField],
    handler: Any,
    functions: Optional[list] = None,
    kind: NodeKind = NodeKind.ACTION,
) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        worker_type=worker_type,
        name=name,
        description=description,
        version=settings.WORKER_VERSION,
        node_class="BUSINESS",
        icon=icon,
        color=settings.WORKER_COLOR,
        tags=_TAGS,
        input_schema=[f.to_dict() for f in inputs],
        output_schema=[f.to_dict() for f in outputs],
        functions=functions or [],
        handler=handler,
        kind=kind,
    )


# ============================================================================
# Handlers (one per node). Sync; raise on failure.
# ============================================================================


def _create_issue_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    project = _require(merged, "project_key", "Project Key")
    issue_type = _require(merged, "issue_type", "Issue Type")
    summary = _require(merged, "summary", "Summary")
    client = _client_from(merged)
    fields: Dict[str, Any] = {
        "project": {"key": project},
        "issuetype": {"name": issue_type},
        "summary": summary,
    }
    desc = text_to_adf(merged.get("description"))
    if desc:
        fields["description"] = desc
    assignee = _opt(merged, "assignee_account_id")
    if assignee:
        fields["assignee"] = {"accountId": assignee}
    priority = _opt(merged, "priority")
    if priority:
        fields["priority"] = {"name": priority}
    labels = _csv(merged.get("labels"))
    if labels:
        fields["labels"] = labels
    parent = _opt(merged, "parent_key")
    if parent:
        fields["parent"] = {"key": parent}
    data = client.create_issue(fields) or {}
    key = data.get("key")
    return _ok(
        issue_key=key,
        issue_id=data.get("id"),
        issue_url=client.browse_url(key) if key else None,
        raw=data,
    )


def _get_issue_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    client = _client_from(merged)
    include_changelog = str(merged.get("include_changelog")).strip().lower() in ("true", "1", "yes", "on")
    expand = "changelog" if include_changelog else None
    data = client.get_issue(key, fields=_opt(merged, "fields"), expand=expand) or {}
    fields = data.get("fields") or {}
    status = (fields.get("status") or {}).get("name")
    return _ok(issue=data, issue_key=data.get("key"), summary=fields.get("summary"), status=status)


def _update_issue_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    client = _client_from(merged)
    fields: Dict[str, Any] = {}
    summary = _opt(merged, "summary")
    if summary:
        fields["summary"] = summary
    if merged.get("description") not in (None, ""):
        fields["description"] = text_to_adf(merged.get("description"))
    assignee = _opt(merged, "assignee_account_id")
    if assignee:
        fields["assignee"] = {"accountId": assignee}
    priority = _opt(merged, "priority")
    if priority:
        fields["priority"] = {"name": priority}
    if merged.get("labels") not in (None, ""):
        fields["labels"] = _csv(merged.get("labels"))
    if merged.get("fields_json") not in (None, ""):
        fields.update(_parse_json_object(merged.get("fields_json"), "fields_json"))
    if not fields:
        raise ValueError("VALIDATION_ERROR: provide at least one field to update")
    client.update_issue(key, fields)
    return _ok(issue_key=key, updated=True)


def _delete_issue_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    client = _client_from(merged)
    client.delete_issue(key, delete_subtasks=_bool(merged.get("delete_subtasks")))
    return _ok(issue_key=key, deleted=True)


def _assign_issue_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    account = _require(merged, "account_id", "Assignee Account Id")
    client = _client_from(merged)
    account_id = None if str(account).lower() in ("null", "none", "unassign") else account
    client.assign_issue(key, account_id)
    return _ok(issue_key=key, assignee_account_id=account_id)


def _search_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    jql = _require(merged, "jql", "JQL")
    client = _client_from(merged)
    fields = _csv(merged.get("fields")) or ["summary", "status", "issuetype", "priority", "assignee", "updated"]
    data = client.search_jql(jql, fields, _int(merged.get("max_results"), 50), _int(merged.get("start_at"), 0)) or {}
    issues = data.get("issues", [])
    return _ok(issues=issues, count=len(issues), raw=data)


def _add_comment_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    adf = text_to_adf(merged.get("body"))
    if not adf:
        raise ValueError("VALIDATION_ERROR: 'body' is required")
    client = _client_from(merged)
    # Optional: nest this comment as a threaded reply under a TOP-LEVEL parent comment.
    parent_id = _opt(merged, "parent_id")
    data = client.add_comment(key, adf, parent_id=parent_id) or {}
    return _ok(issue_key=key, comment_id=data.get("id"), raw=data)


def _get_comments_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    client = _client_from(merged)
    data = client.get_comments(key) or {}
    comments = data.get("comments", [])
    return _ok(issue_key=key, comments=comments, count=len(comments), raw=data)


# -- approved-solution extraction (SA-2415 FixBug) ----------------------------
# The claim-check paradigm stores an array output (get_comments.comments) as a
# separate CSV artifact, so a downstream text ref / worker:code only ever sees a
# file-ref STUB — never the comment bodies. This node does the read + parse where
# the data actually lives (server-side, in the worker) and returns a SCALAR string
# the classify / implement nodes can interpolate inline.


def _adf_to_text(node: Any) -> str:
    """Flatten a Jira Atlassian-Document-Format body (or any fragment) to plain text.
    Walks the content tree, joining text runs and breaking blocks with newlines so the
    approved solution reads naturally for the LLM. Unknown node types are traversed for
    their children, so nothing is silently dropped."""
    parts: List[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for c in n:
                walk(c)
            return
        if not isinstance(n, dict):
            return
        t = n.get("type")
        if t == "text":
            parts.append(str(n.get("text", "")))
        elif t == "hardBreak":
            parts.append("\n")
        elif t == "mention":
            parts.append("@" + str((n.get("attrs") or {}).get("text", "")).lstrip("@"))
        elif t in ("inlineCard", "blockCard"):
            url = str((n.get("attrs") or {}).get("url", ""))
            if url:
                parts.append(url)
        for c in n.get("content", []) or []:
            walk(c)
        if t in ("paragraph", "heading", "listItem", "codeBlock", "blockquote", "rule", "tableRow"):
            parts.append("\n")

    walk(node)
    text = "".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _is_pipeline_comment(text: str) -> bool:
    """True for the FixBug pipeline's OWN auto-comments (started / result), which must
    never count as a human approval or an analysis."""
    head = text.lstrip()
    return (
        head.startswith("\U0001F41B")           # 🐛 FixBug pipeline started
        or head.startswith("AI FixBug")          # AI FixBug · … result
        or "FixBug pipeline started" in text
    )


def _extract_solution_section(analysis_text: str, number: Optional[int]) -> str:
    """When the analysis lists numbered solutions ("Solution 2:", "h4. Solution 2", …),
    return just the approved one. If no numbered section is found (the common single-
    solution case), return the whole analysis text unchanged."""
    if not number:
        return analysis_text
    pat = re.compile(r"(?im)^\s*(?:h[1-6]\.\s*)?solution\s*" + str(number) + r"\b[:.)\-\s]")
    m = pat.search(analysis_text)
    if not m:
        return analysis_text
    start = m.start()
    nxt = re.compile(r"(?im)^\s*(?:h[1-6]\.\s*)?solution\s*" + str(number + 1) + r"\b[:.)\-\s]")
    m2 = nxt.search(analysis_text, m.end())
    return analysis_text[start:(m2.start() if m2 else len(analysis_text))].strip()


def _get_approved_solution_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Surface the reviewer's latest reply and the proposed solution as scalar strings, so
    a downstream classify node can judge APPROVAL INTENT semantically (not by a hard marker).
    Flow: read all comments → drop the pipeline's own auto-comments → ``candidate_reply`` =
    newest human, non-analysis comment → ``approved_solution`` = the ``*Automated analysis*``
    comment at/before it, ADF-flattened (numbered solution sliced when a number is given).
    The ``/implement`` marker is still surfaced (``marker_present`` / ``solution_number``) as
    an explicit-approval shortcut; ``approved`` mirrors it for backward compatibility. Never
    raises — empty fields when there is no reply or no analysis yet."""
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    marker = _opt(merged, "approval_marker") or "/implement"
    analysis_marker = (_opt(merged, "analysis_marker") or "Automated analysis").lower()
    client = _client_from(merged)
    data = client.get_comments(key) or {}
    raw_comments = data.get("comments", []) or []

    # (created, id, text) for every non-pipeline comment, oldest-first.
    enriched = []
    for c in raw_comments:
        text = _adf_to_text(c.get("body"))
        enriched.append({
            "id": str(c.get("id") or ""),
            "created": str(c.get("created") or ""),
            "text": text,
            "pipeline": _is_pipeline_comment(text),
            "analysis": analysis_marker in text.lower(),
        })
    enriched.sort(key=lambda x: x["created"])  # ISO timestamps sort chronologically

    marker_re = re.compile(re.escape(marker) + r"\s*(\d+)?", re.IGNORECASE)

    # Newest analysis comment overall — the top-level parent used to THREAD the pipeline's
    # own comments under, and the source of the solution text.
    latest_analysis = next((c for c in reversed(enriched) if c["analysis"]), None)
    latest_analysis_id = latest_analysis["id"] if latest_analysis else ""

    # Candidate reply = newest human comment that is neither a pipeline auto-comment nor
    # an analysis comment. Its APPROVAL INTENT is judged downstream (semantically, by a
    # classify node) rather than by matching a hard marker here. The ``/implement`` marker
    # is still surfaced (marker_present / solution_number) as an explicit-approval shortcut.
    candidate = next((c for c in reversed(enriched) if not c["pipeline"] and not c["analysis"]), None)
    marker_present = False
    solution_number = None
    if candidate is not None:
        m = marker_re.search(candidate["text"])
        if m:
            marker_present = True
            solution_number = int(m.group(1)) if m.group(1) else None

    # Solution text = the analysis at/before the candidate (else the newest analysis).
    analysis = None
    if candidate is not None:
        analysis = next((c for c in reversed(enriched)
                         if c["analysis"] and c["created"] <= candidate["created"]), None)
    if analysis is None:
        analysis = latest_analysis
    solution = _extract_solution_section(analysis["text"], solution_number) if analysis else ""

    return _ok(
        issue_key=key,
        approved=marker_present,                       # backward-compat: explicit /implement
        marker_present=marker_present,
        candidate_reply=(candidate["text"] if candidate else ""),
        approved_solution=solution,
        solution_number=str(solution_number or ""),
        analysis_comment_id=(analysis["id"] if analysis else latest_analysis_id),
        approval_comment_id=(candidate["id"] if candidate else ""),
        reason=("ok" if (candidate and analysis)
                else "no analysis comment" if candidate else "no candidate reply"),
    )


def _update_comment_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    comment_id = _require(merged, "comment_id", "Comment Id")
    adf = text_to_adf(merged.get("body"))
    if not adf:
        raise ValueError("VALIDATION_ERROR: 'body' is required")
    client = _client_from(merged)
    data = client.update_comment(key, comment_id, adf) or {}
    return _ok(issue_key=key, comment_id=comment_id, raw=data)


def _delete_comment_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    comment_id = _require(merged, "comment_id", "Comment Id")
    client = _client_from(merged)
    client.delete_comment(key, comment_id)
    return _ok(issue_key=key, comment_id=comment_id, deleted=True)


def _get_transitions_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    client = _client_from(merged)
    data = client.get_transitions(key) or {}
    transitions = data.get("transitions", [])
    return _ok(issue_key=key, transitions=transitions, count=len(transitions))


def _transition_issue_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    transition_id = _require(merged, "transition_id", "Transition Id")
    client = _client_from(merged)
    fields: Dict[str, Any] = {}
    resolution = _opt(merged, "resolution")
    if resolution:
        fields["resolution"] = {"name": resolution}
    client.transition_issue(key, str(transition_id), fields or None)
    return _ok(issue_key=key, transition_id=transition_id, transitioned=True)


def _add_worklog_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    time_spent = _require(merged, "time_spent", "Time Spent")
    client = _client_from(merged)
    data = client.add_worklog(key, str(time_spent), _opt(merged, "started"), text_to_adf(merged.get("comment"))) or {}
    return _ok(issue_key=key, worklog_id=data.get("id"), raw=data)


def _get_worklogs_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    client = _client_from(merged)
    data = client.get_worklogs(key) or {}
    logs = data.get("worklogs", [])
    return _ok(issue_key=key, worklogs=logs, count=len(logs), raw=data)


def _link_issues_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    inward = _require(merged, "inward_issue_key", "Inward Issue Key")
    outward = _require(merged, "outward_issue_key", "Outward Issue Key")
    link_type = _require(merged, "link_type", "Link Type")
    client = _client_from(merged)
    client.link_issues(inward, outward, link_type)
    return _ok(inward_issue_key=inward, outward_issue_key=outward, link_type=link_type, linked=True)


def _delete_link_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    link_id = _require(merged, "link_id", "Link Id")
    client = _client_from(merged)
    client.delete_link(str(link_id))
    return _ok(link_id=link_id, deleted=True)


def _add_attachment_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    filename = _require(merged, "filename", "File Name")
    content_b64 = _opt(merged, "content_base64")
    content_text = merged.get("content_text")
    if content_b64:
        try:
            content = base64.b64decode(content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("VALIDATION_ERROR: 'content_base64' is not valid base64") from None
    elif content_text not in (None, ""):
        content = str(content_text).encode("utf-8")
    else:
        raise ValueError("VALIDATION_ERROR: provide 'content_text' or 'content_base64'")
    client = _client_from(merged)
    data = client.add_attachment(key, str(filename), content) or []
    ids = [a.get("id") for a in data]
    return _ok(issue_key=key, attachment_ids=ids, attachments=data, count=len(ids))


def _get_attachments_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    key = _require(merged, "issue_key")
    client = _client_from(merged)
    data = client.get_issue_attachments(key) or {}
    attachments = (data.get("fields") or {}).get("attachment", []) or []
    return _ok(issue_key=key, attachments=attachments, count=len(attachments))


def _list_projects_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    client = _client_from(merged)
    data = client.list_projects(_opt(merged, "query"), _int(merged.get("max_results"), 50)) or {}
    projects = data.get("values", [])
    return _ok(projects=projects, count=len(projects), raw=data)


def _get_project_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    project_key = _require(merged, "project_key", "Project Key")
    client = _client_from(merged)
    data = client.get_project(project_key) or {}
    return _ok(project=data, project_key=data.get("key"), project_name=data.get("name"))


def _myself_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    client = _client_from(merged)
    data = client.myself() or {}
    return _ok(
        account_id=data.get("accountId"),
        display_name=data.get("displayName"),
        email_address=data.get("emailAddress"),
        raw=data,
    )


def _search_users_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    client = _client_from(merged)
    data = client.search_assignable_users(
        (merged.get("query") or "").strip(),
        _opt(merged, "project_key"),
        _opt(merged, "issue_key"),
        _int(merged.get("max_results"), 50),
    ) or []
    return _ok(users=data, count=len(data))


# ============================================================================
# Node definitions (22)
# ============================================================================

NODE_TYPES: List[NodeTypeDefinition] = [
    _node(
        "jira_create_issue", "Jira: Create Issue",
        "Create a Jira issue (POST /rest/api/3/issue). Description is converted to ADF.",
        "FilePlus2",
        [
            text_field("project_key", "Project Key", placeholder="e.g. SA", rules=_req()),
            text_field("issue_type", "Issue Type", placeholder="Task / Bug / Story", default="Task", rules=_req()),
            text_field("summary", "Summary", rules=_req()),
            textarea_field("description", "Description", rows=4),
            text_field("assignee_account_id", "Assignee Account Id", placeholder="Atlassian accountId (optional)"),
            text_field("priority", "Priority", placeholder="e.g. High (optional)"),
            text_field("labels", "Labels", placeholder="comma,separated (optional)"),
            text_field("parent_key", "Parent / Epic Key", placeholder="e.g. SA-1000 (optional)"),
        ],
        _outputs(_o_text("issue_key", "Issue Key"), _o_text("issue_id", "Issue Id"), _o_text("issue_url", "Issue URL"), _o_object("raw", "Raw")),
        _create_issue_handler,
        functions=[list_projects_function, list_issue_types_function, list_assignable_users_function],
    ),
    _node(
        "jira_get_issue", "Jira: Get Issue",
        "Fetch a Jira issue by key (GET /rest/api/3/issue/{key}). Enable "
        "'Include changelog' to also return the field-change history under "
        "issue.changelog.histories (title/summary reqID baseline).",
        "FileSearch",
        [
            _in_issue_key(),
            text_field("fields", "Fields", placeholder="comma-separated field names (optional)"),
            switch_field("include_changelog", "Include changelog (field/title history)", default=False),
        ],
        _outputs(_o_object("issue", "Issue"), _o_text("issue_key", "Issue Key"), _o_text("summary", "Summary"), _o_text("status", "Status")),
        _get_issue_handler,
        kind=NodeKind.READ,
    ),
    _node(
        "jira_update_issue", "Jira: Update Issue",
        "Update fields on a Jira issue (PUT /rest/api/3/issue/{key}).",
        "FilePen",
        [
            _in_issue_key(),
            text_field("summary", "Summary", placeholder="(optional)"),
            textarea_field("description", "Description", rows=4),
            text_field("assignee_account_id", "Assignee Account Id", placeholder="(optional)"),
            text_field("priority", "Priority", placeholder="(optional)"),
            text_field("labels", "Labels", placeholder="comma,separated (optional)"),
            textarea_field("fields_json", "Advanced Fields (JSON)", rows=3, placeholder='{"customfield_10826": {"value": "..."}}'),
        ],
        _outputs(_o_text("issue_key", "Issue Key"), _o_bool("updated", "Updated")),
        _update_issue_handler,
        functions=[list_assignable_users_function],
    ),
    _node(
        "jira_delete_issue", "Jira: Delete Issue",
        "Delete a Jira issue (DELETE /rest/api/3/issue/{key}).",
        "Trash2",
        [_in_issue_key(), switch_field("delete_subtasks", "Delete Subtasks", default=False)],
        _outputs(_o_text("issue_key", "Issue Key"), _o_bool("deleted", "Deleted")),
        _delete_issue_handler,
    ),
    _node(
        "jira_assign_issue", "Jira: Assign Issue",
        "Assign a Jira issue to a user (PUT /rest/api/3/issue/{key}/assignee).",
        "UserPlus",
        [_in_issue_key(), text_field("account_id", "Assignee Account Id", placeholder="accountId, or 'unassign'", rules=_req())],
        _outputs(_o_text("issue_key", "Issue Key"), _o_text("assignee_account_id", "Assignee Account Id")),
        _assign_issue_handler,
        functions=[list_assignable_users_function],
    ),
    _node(
        "jira_search", "Jira: Search (JQL)",
        "Run a JQL search (POST /rest/api/3/search/jql). Count is the length of issues[].",
        "Search",
        [
            textarea_field("jql", "JQL", rows=2, placeholder="project = SA AND statusCategory != Done ORDER BY updated DESC", rules=_req()),
            text_field("fields", "Fields", placeholder="comma-separated (optional)"),
            number_field("max_results", "Max Results", default=50),
            number_field("start_at", "Start At", default=0),
        ],
        _outputs(_o_array("issues", "Issues"), _o_number("count", "Count"), _o_object("raw", "Raw")),
        _search_handler,
        kind=NodeKind.READ,
    ),
    _node(
        "jira_add_comment", "Jira: Add Comment",
        "Add a comment to a Jira issue (POST /rest/api/3/issue/{key}/comment). Body → ADF. "
        "Optional parent_id nests it as a threaded reply under a TOP-LEVEL comment.",
        "MessageSquarePlus",
        [
            _in_issue_key(),
            textarea_field("body", "Comment", rows=4, rules=_req()),
            text_field("parent_id", "Parent Comment Id", placeholder="(optional) nest as a reply"),
        ],
        _outputs(_o_text("issue_key", "Issue Key"), _o_text("comment_id", "Comment Id"), _o_object("raw", "Raw")),
        _add_comment_handler,
    ),
    _node(
        "jira_get_comments", "Jira: Get Comments",
        "List comments on a Jira issue (GET /rest/api/3/issue/{key}/comment).",
        "MessagesSquare",
        [_in_issue_key()],
        _outputs(_o_text("issue_key", "Issue Key"), _o_array("comments", "Comments"), _o_number("count", "Count"), _o_object("raw", "Raw")),
        _get_comments_handler,
        kind=NodeKind.READ,
    ),
    _node(
        "jira_get_approved_solution", "Jira: Get Approved Solution",
        "Reads the ticket's comments and returns ONLY the solution the user approved, as a "
        "scalar string for the classify / implement nodes. Finds the latest reply carrying "
        "the approval marker (default '/implement', optional solution number), takes the "
        "latest '*Automated analysis*' comment at/before it, flattens its ADF body to text, "
        "and (if numbered) slices the approved solution. approved=false when no reply exists.",
        "BadgeCheck",
        [
            _in_issue_key(),
            text_field("approval_marker", "Approval Marker", placeholder="/implement"),
            text_field("analysis_marker", "Analysis Marker", placeholder="Automated analysis"),
        ],
        _outputs(
            _o_text("issue_key", "Issue Key"),
            _o_bool("approved", "Approved (explicit marker)"),
            _o_bool("marker_present", "Marker Present"),
            _o_text("candidate_reply", "Candidate Reply"),
            _o_text("approved_solution", "Approved Solution"),
            _o_text("solution_number", "Solution Number"),
            _o_text("analysis_comment_id", "Analysis Comment Id"),
            _o_text("approval_comment_id", "Approval Comment Id"),
            _o_text("reason", "Reason"),
        ),
        _get_approved_solution_handler,
        kind=NodeKind.READ,
    ),
    _node(
        "jira_update_comment", "Jira: Update Comment",
        "Update a comment on a Jira issue (PUT /rest/api/3/issue/{key}/comment/{id}). Body → ADF.",
        "MessageSquare",
        [_in_issue_key(), text_field("comment_id", "Comment Id", rules=_req()), textarea_field("body", "Comment", rows=4, rules=_req())],
        _outputs(_o_text("issue_key", "Issue Key"), _o_text("comment_id", "Comment Id")),
        _update_comment_handler,
    ),
    _node(
        "jira_delete_comment", "Jira: Delete Comment",
        "Delete a comment on a Jira issue (DELETE /rest/api/3/issue/{key}/comment/{id}).",
        "MessageSquareX",
        [_in_issue_key(), text_field("comment_id", "Comment Id", rules=_req())],
        _outputs(_o_text("issue_key", "Issue Key"), _o_text("comment_id", "Comment Id"), _o_bool("deleted", "Deleted")),
        _delete_comment_handler,
    ),
    _node(
        "jira_get_transitions", "Jira: Get Transitions",
        "List available workflow transitions for an issue (GET /rest/api/3/issue/{key}/transitions).",
        "ListChecks",
        [_in_issue_key()],
        _outputs(_o_text("issue_key", "Issue Key"), _o_array("transitions", "Transitions"), _o_number("count", "Count")),
        _get_transitions_handler,
        kind=NodeKind.READ,
    ),
    _node(
        "jira_transition_issue", "Jira: Transition Issue",
        "Move an issue through a workflow transition (POST /rest/api/3/issue/{key}/transitions).",
        "ArrowRightLeft",
        [
            _in_issue_key(),
            text_field("transition_id", "Transition Id", placeholder="id from 'Get Transitions'", rules=_req()),
            text_field("resolution", "Resolution", placeholder="e.g. Done (optional)"),
        ],
        _outputs(_o_text("issue_key", "Issue Key"), _o_text("transition_id", "Transition Id"), _o_bool("transitioned", "Transitioned")),
        _transition_issue_handler,
        functions=[list_transitions_function],
    ),
    _node(
        "jira_add_worklog", "Jira: Add Worklog",
        "Log work on a Jira issue (POST /rest/api/3/issue/{key}/worklog).",
        "Timer",
        [
            _in_issue_key(),
            text_field("time_spent", "Time Spent", placeholder="e.g. 3h 30m", rules=_req()),
            text_field("started", "Started", placeholder="2026-07-08T09:00:00.000+0000 (optional)"),
            textarea_field("comment", "Comment", rows=2),
        ],
        _outputs(_o_text("issue_key", "Issue Key"), _o_text("worklog_id", "Worklog Id"), _o_object("raw", "Raw")),
        _add_worklog_handler,
    ),
    _node(
        "jira_get_worklogs", "Jira: Get Worklogs",
        "List worklogs on a Jira issue (GET /rest/api/3/issue/{key}/worklog).",
        "Clock",
        [_in_issue_key()],
        _outputs(_o_text("issue_key", "Issue Key"), _o_array("worklogs", "Worklogs"), _o_number("count", "Count"), _o_object("raw", "Raw")),
        _get_worklogs_handler,
        kind=NodeKind.READ,
    ),
    _node(
        "jira_link_issues", "Jira: Link Issues",
        "Create a link between two issues (POST /rest/api/3/issueLink).",
        "Link",
        [
            text_field("inward_issue_key", "Inward Issue Key", placeholder="e.g. SA-1", rules=_req()),
            text_field("outward_issue_key", "Outward Issue Key", placeholder="e.g. SA-2", rules=_req()),
            text_field("link_type", "Link Type", placeholder="e.g. Relates / Blocks", default="Relates", rules=_req()),
        ],
        _outputs(_o_text("inward_issue_key", "Inward"), _o_text("outward_issue_key", "Outward"), _o_text("link_type", "Link Type"), _o_bool("linked", "Linked")),
        _link_issues_handler,
    ),
    _node(
        "jira_delete_link", "Jira: Delete Link",
        "Delete an issue link by id (DELETE /rest/api/3/issueLink/{id}).",
        "Link2Off",
        [text_field("link_id", "Link Id", rules=_req())],
        _outputs(_o_text("link_id", "Link Id"), _o_bool("deleted", "Deleted")),
        _delete_link_handler,
    ),
    _node(
        "jira_add_attachment", "Jira: Add Attachment",
        "Attach a file to a Jira issue (POST /rest/api/3/issue/{key}/attachments). Provide text or base64 content.",
        "Paperclip",
        [
            _in_issue_key(),
            text_field("filename", "File Name", placeholder="e.g. report.txt", rules=_req()),
            textarea_field("content_text", "Text Content", rows=3, placeholder="Plain-text file content (or use Base64 below)"),
            textarea_field("content_base64", "Base64 Content", rows=3, placeholder="Base64-encoded binary content"),
        ],
        _outputs(_o_text("issue_key", "Issue Key"), _o_array("attachment_ids", "Attachment Ids"), _o_array("attachments", "Attachments"), _o_number("count", "Count")),
        _add_attachment_handler,
    ),
    _node(
        "jira_get_attachments", "Jira: Get Attachments",
        "List attachments on a Jira issue (GET /rest/api/3/issue/{key}?fields=attachment).",
        "Files",
        [_in_issue_key()],
        _outputs(_o_text("issue_key", "Issue Key"), _o_array("attachments", "Attachments"), _o_number("count", "Count")),
        _get_attachments_handler,
        kind=NodeKind.READ,
    ),
    _node(
        "jira_list_projects", "Jira: List Projects",
        "List/search Jira projects (GET /rest/api/3/project/search).",
        "FolderKanban",
        [text_field("query", "Filter", placeholder="(optional)"), number_field("max_results", "Max Results", default=50)],
        _outputs(_o_array("projects", "Projects"), _o_number("count", "Count"), _o_object("raw", "Raw")),
        _list_projects_handler,
        functions=[list_projects_function],
        kind=NodeKind.READ,
    ),
    _node(
        "jira_get_project", "Jira: Get Project",
        "Fetch a single Jira project (GET /rest/api/3/project/{key}).",
        "Folder",
        [text_field("project_key", "Project Key", placeholder="e.g. SA", rules=_req())],
        _outputs(_o_object("project", "Project"), _o_text("project_key", "Project Key"), _o_text("project_name", "Project Name")),
        _get_project_handler,
        functions=[list_projects_function],
        kind=NodeKind.READ,
    ),
    _node(
        "jira_myself", "Jira: Test / Whoami",
        "Return the authenticated user (GET /rest/api/3/myself) — a connectivity + credential check.",
        "UserCheck",
        [],
        _outputs(_o_text("account_id", "Account Id"), _o_text("display_name", "Display Name"), _o_text("email_address", "Email"), _o_object("raw", "Raw")),
        _myself_handler,
        functions=[test_connection_function],
        kind=NodeKind.UTIL,
    ),
    _node(
        "jira_search_users", "Jira: Search Users",
        "Search assignable users (GET /rest/api/3/user/assignable/search).",
        "Users",
        [
            text_field("query", "Search", placeholder="name or email (optional)"),
            text_field("project_key", "Project Key", placeholder="(optional)"),
            text_field("issue_key", "Issue Key", placeholder="(optional)"),
            number_field("max_results", "Max Results", default=50),
        ],
        _outputs(_o_array("users", "Users"), _o_number("count", "Count")),
        _search_users_handler,
        functions=[list_assignable_users_function],
        kind=NodeKind.READ,
    ),
]
