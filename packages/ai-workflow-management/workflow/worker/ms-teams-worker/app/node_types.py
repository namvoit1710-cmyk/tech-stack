"""SA-1549 — multi-type node registry for the ms-teams-worker.

Exposes TWO node types from one worker process (worker-sdk ``node_types=[...]``):

* ``ms_teams`` — the EXISTING webhook node (send message / adaptive card),
  UNCHANGED. Its handler is a thin adapter that re-runs the existing
  ``ExecuteTaskUseCase``, so behavior is byte-for-byte the same (parity guarded
  by tests). Its design-time ``test_webhook`` function is preserved.
* ``ms_teams_meeting`` — NEW. Creates a Teams meeting via a Microsoft Graph
  calendar event (``Calendars.ReadWrite`` application permission), auth from the
  vault ``generic-oauth2-app`` credential (client-credentials). Reuses the Graph
  token plumbing shape shipped in email-worker.

Registering node_types replaces the SDK's single base registration
(worker_server ``__init__`` registers node_types OR the base WORKER_TYPE, not
both) — so ``ms_teams`` MUST be listed here to keep the webhook node alive.
This mirrors the SA-1078 mapping-data-worker pattern.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition
from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField, ConditionConfig, ConditionOperator, FieldConfig, FieldControl,
    FieldWrapper, OutputType, ValidationRule, select_field, switch_field, text_field,
)
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth

from app.functions import MS_TEAMS_WORKER_FUNCTIONS
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    MS_TEAMS_INPUT_SCHEMA,
    MS_TEAMS_OUTPUT_SCHEMA,
    ExecuteTaskUseCase,
)
from app.layer2_application.services.graph_meeting_client import (
    GraphMeetingClient,
    GraphMeetingError,
)
from app.layer2_application.services.graph_channel_client import (
    GraphChannelClient,
    GraphChannelError,
)
from app.layer4_frameworks.config import settings
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)


# --------------------------------------------------------------------------
# ms_teams (existing webhook node) — parity adapter
# --------------------------------------------------------------------------
class _NoopLogger:
    def info(self, *a: Any, **k: Any) -> None: ...
    def error(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...
    def debug(self, *a: Any, **k: Any) -> None: ...


class _NoopMonitor:
    def track(self, *a: Any, **k: Any) -> None: ...


def _ms_teams_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Adapter: run the EXISTING webhook use case unchanged (byte-for-byte parity)."""
    uc = ExecuteTaskUseCase(logger=_NoopLogger(), monitor=_NoopMonitor())
    res = uc.execute(ExecuteTaskCommand(
        task_id=str(inputs.get("_task_id", "ms_teams")),
        action="execute",
        inputs=inputs,
        parameters=parameters or {},
    ))
    status = getattr(res.status, "value", res.status)
    if str(status).lower() == "error":
        # Preserve the error_code by prefixing it onto the raised message; the
        # multi-type route reports raised handlers as TaskStatus.ERROR.
        code = (res.outputs or {}).get("error_code") or "WEBHOOK_ERROR"
        raise RuntimeError(f"{code}: {res.error or 'ms_teams task failed'}")
    return res.outputs


# --------------------------------------------------------------------------
# ms_teams_meeting (new) — Graph calendar event
# --------------------------------------------------------------------------
_MEETING_INPUT_FIELDS: List[AutoFormField] = [
    text_field(
        key="organizer",
        label="Organizer (UPN or user id)",
        placeholder="organizer@contoso.com",
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        key="subject",
        label="Subject",
        placeholder="Governance review",
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        key="start",
        label="Start (ISO 8601)",
        placeholder="2026-07-10T09:00:00",
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        key="end",
        label="End (ISO 8601)",
        placeholder="2026-07-10T09:30:00",
        rules=[ValidationRule(method="required")],
    ),
    text_field(
        key="time_zone",
        label="Time Zone",
        placeholder="UTC",
        default="UTC",
    ),
    AutoFormField(
        key="body",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Body"},
            control_props={"placeholder": "Optional agenda (HTML supported)", "rowsLength": 3},
        ),
    ),
    # Manual mode: add attendees one at a time. Good for 1-2 ad-hoc addresses.
    AutoFormField(
        key="attendees",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_wrapper=FieldWrapper.SEQUENCE,
            wrapper_props={"label": "Attendees (add individually)", "isAdd": True, "isDelete": True},
        ),
        fields=[
            AutoFormField(
                key="0",
                output_type=OutputType.STRING,
                field_config=FieldConfig(
                    field_control=FieldControl.INPUT,
                    field_wrapper=FieldWrapper.FORM_ITEM,
                    wrapper_props={"label": "E-mail"},
                    control_props={"placeholder": "steward@contoso.com"},
                ),
            ),
        ],
    ),
    # SA-1557 — list/expression mode: bind a WHOLE list of attendees from an upstream
    # node's output (a CODE control accepts a JSON array or an expression like
    # {{queryNode.output.recipients}} that resolves to an array at run time). Same
    # array-input pattern as mapping-data-worker. Merged with the manual list above.
    AutoFormField(
        key="attendees_list",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.CODE_CONTROL,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Attendees from a list / upstream node"},
            control_props={
                "placeholder": '["a@contoso.com", "b@contoso.com"]  or  {{queryNode.output.recipients}}',
            },
        ),
    ),
    switch_field(key="allow_new_time_proposals", label="Allow New Time Proposals", default=True),
]

MEETING_INPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in _MEETING_INPUT_FIELDS]

MEETING_OUTPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in [
    AutoFormField(
        key="success",
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH, field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Success"}, control_props={"readonly": True},
        ),
    ),
    text_field(key="event_id", label="Event Id", readonly=True),
    text_field(key="join_url", label="Join URL", readonly=True),
    text_field(key="web_link", label="Web Link", readonly=True),
    text_field(key="error", label="Error", readonly=True),
    text_field(key="error_code", label="Error Code", readonly=True),
]]

_MEETING_SCOPE = "https://graph.microsoft.com/.default"


def _coerce_email(item: Any) -> str:
    """Coerce one attendee row to an e-mail string. Returns '' for empty/None (skip);
    raises ValueError for a non-empty row that can't be coerced (SA-1557: never silently
    drop a value the user meant to send)."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        # rows may arrive as {"0": "a@b.com"} (SEQUENCE), {"email"/"address"/"value": ...}
        val = (item.get("email") or item.get("address") or item.get("value")
               or item.get("upn") or next(iter(item.values()), ""))
        if isinstance(val, str):
            return val.strip()
        if val in (None, ""):
            return ""
        raise ValueError(f"VALIDATION_ERROR: attendee row is not an e-mail: {item!r}")
    raise ValueError(f"VALIDATION_ERROR: attendee row is not an e-mail: {item!r}")


def _as_email_list(v: Any) -> List[str]:
    """Normalize an attendees value to a de-duped e-mail string[].

    Accepts: a manual SEQUENCE (list of str/dict rows), an upstream/expression-resolved
    array (list), or a CODE-control JSON string like '["a@x.com", ...]'. A bare string
    is treated as a single e-mail (or comma-separated list). Non-coercible non-empty
    rows raise rather than drop silently (SA-1557)."""
    if v is None or v == "":
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # CODE control may hand us a JSON array string; else treat as a single/CSV value.
        if s[0] in "[{":
            try:
                return _as_email_list(json.loads(s))
            except (ValueError, TypeError):
                raise ValueError(f"VALIDATION_ERROR: attendees_list is not valid JSON: {s[:60]!r}")
        parts = [p.strip() for p in s.split(",")] if "," in s else [s]
        return [p for p in parts if p]
    if isinstance(v, dict):
        e = _coerce_email(v)
        return [e] if e else []
    if isinstance(v, list):
        out: List[str] = []
        for item in v:
            e = _coerce_email(item)
            if e:
                out.append(e)
        return out
    raise ValueError(f"VALIDATION_ERROR: attendees must be a list/array, got {type(v).__name__}")


def _graph_token_from_credential(client: GraphMeetingClient, cred: Dict[str, Any]) -> str:
    """Resolve a Graph token from the vault credential (generic-oauth2-app / bearer)."""
    cred_type = cred.get("type")
    config = cred.get("config") or {}
    if cred_type == "bearer" and config.get("token"):
        return str(config["token"])
    if cred_type == "json" and config.get("tokenUrl") and config.get("clientId"):
        return client.fetch_token(
            token_url=str(config["tokenUrl"]),
            client_id=str(config["clientId"]),
            client_secret=str(config.get("clientSecret") or ""),
            scopes=str(config.get("scopes") or ""),
        )
    raise ValueError(
        f"credential type '{cred_type}' is not usable for Microsoft Graph "
        "(use a Generic OAuth2 Application credential)"
    )


def _meeting_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Teams meeting via a Graph calendar event. Raises on failure."""
    merged = {**(parameters or {}), **inputs}

    organizer = (merged.get("organizer") or "").strip()
    subject = (merged.get("subject") or "").strip()
    start = (merged.get("start") or "").strip()
    end = (merged.get("end") or "").strip()
    if not organizer:
        raise ValueError("VALIDATION_ERROR: 'organizer' is required")
    if not subject:
        raise ValueError("VALIDATION_ERROR: 'subject' is required")
    if not start or not end:
        raise ValueError("VALIDATION_ERROR: 'start' and 'end' are required")

    # SA-1557: attendees come from the manual SEQUENCE and/or an upstream list/expression
    # (attendees_list). Merge both, de-dupe preserving order, case-insensitively.
    attendees: List[str] = []
    seen: set = set()
    for src in (_as_email_list(merged.get("attendees")), _as_email_list(merged.get("attendees_list"))):
        for e in src:
            key = e.lower()
            if key not in seen:
                seen.add(key)
                attendees.append(e)

    # T5: auth comes only from the vault credential; fail closed when missing.
    cred = resolve_effective_auth(merged, require=True)

    client = GraphMeetingClient()
    try:
        token = _graph_token_from_credential(client, cred)
        result = client.create_event(
            access_token=token,
            organizer=organizer,
            subject=subject,
            start=start,
            end=end,
            time_zone=(merged.get("time_zone") or "UTC").strip() or "UTC",
            body=merged.get("body") or "",
            attendees=attendees,
            allow_new_time_proposals=bool(merged.get("allow_new_time_proposals", True)),
        )
    except GraphMeetingError as e:
        raise RuntimeError(f"{e.error_code or 'GRAPH_ERROR'}: {e}") from e

    return {
        "success": True,
        "event_id": result.get("event_id"),
        "join_url": result.get("join_url"),
        "web_link": result.get("web_link"),
        "error": None,
        "error_code": None,
    }


MS_TEAMS_NODE_DEF = NodeTypeDefinition(
    worker_type=settings.WORKER_TYPE,  # "ms_teams" — unchanged
    name=settings.WORKER_NAME,
    description=settings.WORKER_DESCRIPTION,
    version=settings.WORKER_VERSION,
    node_class=settings.WORKER_NODE_CLASS,
    icon=settings.WORKER_ICON,
    color=settings.WORKER_COLOR,
    tags=settings.get_tags_list(),
    input_schema=MS_TEAMS_INPUT_SCHEMA,
    output_schema=MS_TEAMS_OUTPUT_SCHEMA,
    functions=MS_TEAMS_WORKER_FUNCTIONS,
    handler=_ms_teams_handler,
)

MS_TEAMS_MEETING_NODE_DEF = NodeTypeDefinition(
    worker_type="ms_teams_meeting",
    name="Teams Meeting",
    description="Create a Microsoft Teams meeting (calendar event) via Microsoft Graph.",
    version=settings.WORKER_VERSION,
    node_class="BUSINESS",
    icon="CalendarPlus",
    color=settings.WORKER_COLOR,
    tags=["teams", "microsoft", "meeting", "calendar", "graph"],
    input_schema=MEETING_INPUT_SCHEMA,
    output_schema=MEETING_OUTPUT_SCHEMA,
    handler=_meeting_handler,
)

# --------------------------------------------------------------------------
# ms_teams_channel (new, SA-1550) — Graph channel admin
# --------------------------------------------------------------------------
_OP_CREATE = "create_channel"
_OP_ADD_MEMBER = "add_member"
_OP_LIST = "list_channels"


def _when_op(value: str) -> ConditionConfig:
    """Show the field only when operation_type == value (NOT_EQUALS → hide otherwise)."""
    return ConditionConfig(
        field_key="operation_type",
        operator=ConditionOperator.NOT_EQUALS,
        condition_value=value,
    )


def _text_when(key: str, label: str, op: str, placeholder: str = "", required: bool = False) -> AutoFormField:
    wrapper = {"label": label}
    if required:
        wrapper["required"] = True
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props=wrapper,
            control_props={"placeholder": placeholder} if placeholder else None,
            rules=[ValidationRule(method="required")] if required else None,
            condition=_when_op(op),
        ),
    )


_CHANNEL_INPUT_FIELDS: List[AutoFormField] = [
    select_field(
        key="operation_type",
        label="Operation",
        options=[
            (_OP_CREATE, "Create Channel"),
            (_OP_ADD_MEMBER, "Add Member"),
            (_OP_LIST, "List Channels"),
        ],
        rules=[ValidationRule(method="required")],
    ),
    # team_id is required for every op. (SA-1551 will turn this into a live
    # dropdown via the list_teams function; until then it is a text input.)
    text_field(
        key="team_id",
        label="Team Id",
        placeholder="AAD group id of the team",
        rules=[ValidationRule(method="required")],
    ),
    # create_channel
    _text_when("display_name", "Channel Name", _OP_CREATE, "e.g. CR-1234 War Room", required=True),
    _text_when("description", "Description", _OP_CREATE, "Optional"),
    AutoFormField(
        key="membership_type",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.SELECT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Membership Type"},
            control_props={"options": [
                {"id": "standard", "value": "Standard"},
                {"id": "private", "value": "Private"},
            ]},
            condition=_when_op(_OP_CREATE),
        ),
    ),
    _text_when("owner_user_id", "Owner User Id (private only)", _OP_CREATE, "AAD id of the channel owner"),
    # add_member
    _text_when("user_id", "User Id", _OP_ADD_MEMBER, "AAD id / UPN of the user", required=True),
    AutoFormField(
        key="owner",
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Add as Owner"},
            condition=_when_op(_OP_ADD_MEMBER),
        ),
    ),
]

CHANNEL_INPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in _CHANNEL_INPUT_FIELDS]

CHANNEL_OUTPUT_SCHEMA: List[Dict[str, Any]] = [f.to_dict() for f in [
    AutoFormField(
        key="success", output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH, field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Success"}, control_props={"readonly": True},
        ),
    ),
    text_field(key="channel_id", label="Channel Id", readonly=True),
    text_field(key="web_url", label="Web URL", readonly=True),
    text_field(key="membership_id", label="Membership Id", readonly=True),
    AutoFormField(
        key="channels", output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA, field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Channels"}, control_props={"readonly": True},
        ),
    ),
    text_field(key="error", label="Error", readonly=True),
    text_field(key="error_code", label="Error Code", readonly=True),
]]


def _graph_channel_token(client: GraphChannelClient, cred: Dict[str, Any]) -> str:
    cred_type = cred.get("type")
    config = cred.get("config") or {}
    if cred_type == "bearer" and config.get("token"):
        return str(config["token"])
    if cred_type == "json" and config.get("tokenUrl") and config.get("clientId"):
        return client.fetch_token(
            token_url=str(config["tokenUrl"]),
            client_id=str(config["clientId"]),
            client_secret=str(config.get("clientSecret") or ""),
            scopes=str(config.get("scopes") or ""),
        )
    raise ValueError(
        f"credential type '{cred_type}' is not usable for Microsoft Graph "
        "(use a Generic OAuth2 Application credential)"
    )


def _channel_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Channel admin ops behind one operation_type select. Raises on failure."""
    merged = {**(parameters or {}), **inputs}
    op = (merged.get("operation_type") or "").strip()
    team_id = (merged.get("team_id") or "").strip()
    if op not in (_OP_CREATE, _OP_ADD_MEMBER, _OP_LIST):
        raise ValueError(f"VALIDATION_ERROR: unsupported operation_type '{op}'")
    if not team_id:
        raise ValueError("VALIDATION_ERROR: 'team_id' is required")

    cred = resolve_effective_auth(merged, require=True)
    client = GraphChannelClient()
    try:
        token = _graph_channel_token(client, cred)
        if op == _OP_CREATE:
            name = (merged.get("display_name") or "").strip()
            if not name:
                raise ValueError("VALIDATION_ERROR: 'display_name' is required for create_channel")
            res = client.create_channel(
                access_token=token, team_id=team_id, display_name=name,
                description=merged.get("description") or "",
                membership_type=(merged.get("membership_type") or "standard").strip() or "standard",
                owner_user_id=(merged.get("owner_user_id") or "").strip(),
            )
            return {"success": True, "channel_id": res.get("channel_id"), "web_url": res.get("web_url"),
                    "membership_id": None, "channels": None, "error": None, "error_code": None}
        if op == _OP_ADD_MEMBER:
            user_id = (merged.get("user_id") or "").strip()
            if not user_id:
                raise ValueError("VALIDATION_ERROR: 'user_id' is required for add_member")
            res = client.add_member(access_token=token, team_id=team_id, user_id=user_id,
                                    owner=bool(merged.get("owner", False)))
            return {"success": True, "channel_id": None, "web_url": None,
                    "membership_id": res.get("membership_id"), "channels": None, "error": None, "error_code": None}
        # list_channels
        res = client.list_channels(access_token=token, team_id=team_id)
        return {"success": True, "channel_id": None, "web_url": None, "membership_id": None,
                "channels": res.get("channels"), "error": None, "error_code": None}
    except GraphChannelError as e:
        raise RuntimeError(f"{e.error_code or 'GRAPH_ERROR'}: {e}") from e


# Design-time functions (consumed by SA-1551 credential-aware dynamic dropdowns).
async def list_teams_fn(params: Dict[str, Any]) -> Dict[str, Any]:
    """List teams for the team_id picker. Consumes __credentials at design time."""
    cred = resolve_effective_auth(params, require=True)
    client = GraphChannelClient()
    try:
        token = _graph_channel_token(client, cred)
        teams = client.list_teams(access_token=token)
    except GraphChannelError as e:
        return {"success": False, "error": str(e), "error_code": e.error_code}
    return {"success": True, "options": teams}


async def list_channels_fn(params: Dict[str, Any]) -> Dict[str, Any]:
    """List channels of a team for the channel picker (depends on team_id)."""
    team_id = (params.get("team_id") or "").strip()
    if not team_id:
        return {"success": False, "error": "'team_id' is required", "error_code": "VALIDATION_ERROR"}
    cred = resolve_effective_auth(params, require=True)
    client = GraphChannelClient()
    try:
        token = _graph_channel_token(client, cred)
        res = client.list_channels(access_token=token, team_id=team_id)
    except GraphChannelError as e:
        return {"success": False, "error": str(e), "error_code": e.error_code}
    return {"success": True, "options": res.get("channels", [])}


MS_TEAMS_CHANNEL_FUNCTIONS = [
    WorkerFunction(
        name="list_teams",
        description="List Teams the app can administer (design-time picker for team_id).",
        input_schema=[],
        output_schema=[{"key": "success", "type": "boolean"}, {"key": "options", "type": "array"}],
        handler=list_teams_fn,
    ),
    WorkerFunction(
        name="list_channels",
        description="List channels of a team (design-time picker; depends on team_id).",
        input_schema=[{"key": "team_id", "type": "string", "required": True, "label": "Team Id"}],
        output_schema=[{"key": "success", "type": "boolean"}, {"key": "options", "type": "array"}],
        handler=list_channels_fn,
    ),
]

MS_TEAMS_CHANNEL_NODE_DEF = NodeTypeDefinition(
    worker_type="ms_teams_channel",
    name="Teams Channel Admin",
    description="Create channels, add members, and list channels in a Microsoft Teams team via Graph.",
    version=settings.WORKER_VERSION,
    node_class="BUSINESS",
    icon="Users",
    color=settings.WORKER_COLOR,
    tags=["teams", "microsoft", "channel", "admin", "graph"],
    input_schema=CHANNEL_INPUT_SCHEMA,
    output_schema=CHANNEL_OUTPUT_SCHEMA,
    functions=MS_TEAMS_CHANNEL_FUNCTIONS,
    handler=_channel_handler,
)

NODE_TYPES = [MS_TEAMS_NODE_DEF, MS_TEAMS_MEETING_NODE_DEF, MS_TEAMS_CHANNEL_NODE_DEF]
