"""Jira Cloud REST API v3 client for the jira-worker.

One synchronous client, one method per Jira operation. All node handlers build a
client from the vault-resolved credential and call these methods; the SDK routes
run sync handlers off the event loop (``asyncio.to_thread``), so a blocking
``httpx.Client`` here is fine.

Auth: the ``jira-api-token`` vault credential resolves to
``{"type": "json", "config": {"domain", "email", "apiToken"}}`` and we build
HTTP Basic (``base64(email:apiToken)``) — the standard Jira Cloud REST v3 scheme.
For resilience we also accept a ``basic`` credential (username/password) and a
``bearer`` credential (Server/DC PAT).
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

# Jira Cloud REST API v3 root, relative to the site domain.
_API_ROOT = "/rest/api/3"
_DEFAULT_TIMEOUT = 30.0


class JiraError(Exception):
    """A Jira API or configuration error. ``error_code`` drives the node's error_code."""

    def __init__(
        self,
        message: str,
        error_code: str = "JIRA_ERROR",
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


def text_to_adf(text: Any) -> Optional[dict[str, Any]]:
    """Convert plain text (or pass through an ADF doc) to Atlassian Document Format.

    - ``None`` / ``""`` → ``None`` (caller omits the field).
    - a ``dict`` that already looks like ADF (``type == "doc"``) → returned as-is.
    - a JSON string that parses to an ADF doc → used verbatim (power users may
      paste raw ADF).
    - any other string → one ADF paragraph per line.
    """
    if text is None:
        return None
    if isinstance(text, dict):
        return text
    s = str(text)
    if not s.strip():
        return None
    stripped = s.lstrip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict) and parsed.get("type") == "doc":
                return parsed
        except (ValueError, TypeError):
            pass  # not ADF JSON — treat as plain text below
    content: list[dict[str, Any]] = []
    for line in s.split("\n"):
        if line:
            content.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
        else:
            content.append({"type": "paragraph"})
    return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph"}]}


class JiraClient:
    """Thin synchronous wrapper over the Jira Cloud REST API v3."""

    def __init__(
        self,
        base_url: str,
        auth: Optional[tuple[str, str]] = None,
        bearer: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._auth = auth
        self._bearer = bearer
        self._timeout = timeout

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_credential(cls, cred: Optional[dict[str, Any]], timeout: float = _DEFAULT_TIMEOUT) -> "JiraClient":
        """Build a client from a vault-resolved ``{type, config}`` credential.

        Raises ``JiraError(AUTH_ERROR)`` if the credential does not carry a usable
        Jira site + secret. The credential is the single source of truth (T5).
        """
        if not isinstance(cred, dict):
            raise JiraError(
                "a Jira credential is required: set a credential_ref on the node",
                error_code="AUTH_ERROR",
            )
        cred_type = cred.get("type")
        config = cred.get("config") or {}

        # Site URL: jira-api-token uses `domain`; generic creds use `baseUrl`.
        base_url = str(config.get("domain") or config.get("baseUrl") or "").strip()
        if not base_url:
            raise JiraError(
                "the Jira credential is missing the site URL (domain)",
                error_code="AUTH_ERROR",
            )
        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url

        # jira-api-token (JSON blob) → email + apiToken → HTTP Basic.
        if cred_type in ("json", "basic"):
            email = str(config.get("email") or config.get("username") or "").strip()
            token = str(config.get("apiToken") or config.get("password") or "").strip()
            if email and token:
                return cls(base_url=base_url, auth=(email, token), timeout=timeout)
            # A bare token in a basic/json blob (Server/DC PAT stored as password).
            if token:
                return cls(base_url=base_url, bearer=token, timeout=timeout)
        # bearer credential (Server/DC personal access token).
        if cred_type == "bearer" and config.get("token"):
            return cls(base_url=base_url, bearer=str(config["token"]), timeout=timeout)
        # api_key credential (token in `value`).
        if cred_type == "api_key" and config.get("value"):
            return cls(base_url=base_url, bearer=str(config["value"]), timeout=timeout)

        raise JiraError(
            f"credential type '{cred_type}' is not usable for Jira "
            "(use a Jira API Token credential: site domain + email + API token)",
            error_code="AUTH_ERROR",
        )

    @property
    def base_url(self) -> str:
        return self._base

    def browse_url(self, issue_key: str) -> str:
        return f"{self._base}/browse/{issue_key}"

    # -- transport ------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        files: Optional[Any] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """Issue a request against the REST v3 API and return parsed JSON (or ``None``).

        ``path`` is relative to ``/rest/api/3`` (e.g. ``/issue/SA-1``). Absolute
        ``/rest/...`` paths are honored as-is. Raises ``JiraError`` on >= 400.
        """
        if path.startswith("/rest/"):
            url = f"{self._base}{path}"
        else:
            url = f"{self._base}{_API_ROOT}{path}"

        headers: dict[str, str] = {"Accept": "application/json"}
        if self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"
        if extra_headers:
            headers.update(extra_headers)

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    files=files,
                    headers=headers,
                    auth=self._auth if self._auth else None,
                )
        except httpx.TimeoutException as exc:
            raise JiraError(f"Jira request timed out: {exc}", error_code="TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise JiraError(f"Jira request failed: {exc}", error_code="NETWORK_ERROR") from exc

        if resp.status_code >= 400:
            raise JiraError(
                self._error_message(resp),
                error_code=self._error_code(resp.status_code),
                status_code=resp.status_code,
            )

        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _error_code(status: int) -> str:
        if status in (401, 403):
            return "AUTH_ERROR"
        if status == 404:
            return "NOT_FOUND"
        if status == 429:
            return "RATE_LIMIT_EXCEEDED"
        if 400 <= status < 500:
            return "VALIDATION_ERROR"
        return "JIRA_ERROR"

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        prefix = f"Jira API {resp.status_code}"
        try:
            data = resp.json()
        except ValueError:
            text = resp.text.strip()
            return f"{prefix}: {text[:300]}" if text else prefix
        parts: list[str] = []
        if isinstance(data, dict):
            for msg in data.get("errorMessages") or []:
                parts.append(str(msg))
            errors = data.get("errors")
            if isinstance(errors, dict):
                parts.extend(f"{k}: {v}" for k, v in errors.items())
        return f"{prefix}: {'; '.join(parts)}" if parts else prefix

    # -- issue operations -----------------------------------------------------

    def myself(self) -> Any:
        return self._request("GET", "/myself")

    def get_issue(
        self, issue_key: str, fields: Optional[str] = None, expand: Optional[str] = None
    ) -> Any:
        """Fetch an issue (GET /issue/{key}).

        ``expand="changelog"`` also returns the field-change history under
        ``changelog.histories`` (each item has ``field``/``fromString``/``toString``),
        which is how the title (summary) reqID baseline is derived downstream.
        """
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = fields
        if expand:
            params["expand"] = expand
        return self._request("GET", f"/issue/{issue_key}", params=params or None)

    def search_jql(
        self,
        jql: str,
        fields: Optional[list[str]] = None,
        max_results: int = 50,
        start_at: int = 0,
    ) -> Any:
        body: dict[str, Any] = {"jql": jql, "maxResults": max_results, "startAt": start_at}
        if fields:
            body["fields"] = fields
        return self._request("POST", "/search/jql", json_body=body)

    def create_issue(self, fields: dict[str, Any]) -> Any:
        return self._request("POST", "/issue", json_body={"fields": fields})

    def update_issue(self, issue_key: str, fields: dict[str, Any]) -> Any:
        return self._request("PUT", f"/issue/{issue_key}", json_body={"fields": fields})

    def delete_issue(self, issue_key: str, delete_subtasks: bool = False) -> Any:
        params = {"deleteSubtasks": "true" if delete_subtasks else "false"}
        return self._request("DELETE", f"/issue/{issue_key}", params=params)

    def assign_issue(self, issue_key: str, account_id: Optional[str]) -> Any:
        # accountId=None → unassign; "-1" → default assignee (Jira semantics).
        return self._request("PUT", f"/issue/{issue_key}/assignee", json_body={"accountId": account_id})

    # -- comment operations ---------------------------------------------------

    def add_comment(
        self, issue_key: str, body_adf: dict[str, Any], parent_id: str | None = None
    ) -> Any:
        payload: dict[str, Any] = {"body": body_adf}
        if parent_id:
            # Jira threaded comments: nest this comment under a TOP-LEVEL parent comment.
            # Jira only allows one level (a reply cannot itself be a parent).
            payload["parentId"] = str(parent_id)
        return self._request("POST", f"/issue/{issue_key}/comment", json_body=payload)

    def get_comments(self, issue_key: str) -> Any:
        return self._request("GET", f"/issue/{issue_key}/comment")

    def update_comment(self, issue_key: str, comment_id: str, body_adf: dict[str, Any]) -> Any:
        return self._request(
            "PUT", f"/issue/{issue_key}/comment/{comment_id}", json_body={"body": body_adf}
        )

    def delete_comment(self, issue_key: str, comment_id: str) -> Any:
        return self._request("DELETE", f"/issue/{issue_key}/comment/{comment_id}")

    # -- transition operations ------------------------------------------------

    def get_transitions(self, issue_key: str) -> Any:
        return self._request("GET", f"/issue/{issue_key}/transitions")

    def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        fields: Optional[dict[str, Any]] = None,
    ) -> Any:
        body: dict[str, Any] = {"transition": {"id": transition_id}}
        if fields:
            body["fields"] = fields
        return self._request("POST", f"/issue/{issue_key}/transitions", json_body=body)

    # -- worklog operations ---------------------------------------------------

    def add_worklog(
        self,
        issue_key: str,
        time_spent: str,
        started: Optional[str] = None,
        comment_adf: Optional[dict[str, Any]] = None,
    ) -> Any:
        body: dict[str, Any] = {"timeSpent": time_spent}
        if started:
            body["started"] = started
        if comment_adf:
            body["comment"] = comment_adf
        return self._request("POST", f"/issue/{issue_key}/worklog", json_body=body)

    def get_worklogs(self, issue_key: str) -> Any:
        return self._request("GET", f"/issue/{issue_key}/worklog")

    # -- issue link operations ------------------------------------------------

    def link_issues(self, inward_key: str, outward_key: str, link_type: str) -> Any:
        body = {
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_key},
            "outwardIssue": {"key": outward_key},
        }
        return self._request("POST", "/issueLink", json_body=body)

    def delete_link(self, link_id: str) -> Any:
        return self._request("DELETE", f"/issueLink/{link_id}")

    def list_link_types(self) -> Any:
        return self._request("GET", "/issueLinkType")

    # -- attachment operations ------------------------------------------------

    def add_attachment(self, issue_key: str, filename: str, content: bytes, content_type: str = "application/octet-stream") -> Any:
        # Attachments require the XSRF-check opt-out header and multipart form.
        files = {"file": (filename, content, content_type)}
        return self._request(
            "POST",
            f"/issue/{issue_key}/attachments",
            files=files,
            extra_headers={"X-Atlassian-Token": "no-check"},
        )

    def get_issue_attachments(self, issue_key: str) -> Any:
        return self._request("GET", f"/issue/{issue_key}", params={"fields": "attachment"})

    # -- project / user operations --------------------------------------------

    def list_projects(self, query: Optional[str] = None, max_results: int = 50) -> Any:
        params: dict[str, Any] = {"maxResults": max_results}
        if query:
            params["query"] = query
        return self._request("GET", "/project/search", params=params)

    def get_project(self, project_key: str) -> Any:
        return self._request("GET", f"/project/{project_key}")

    def list_issue_types(self) -> Any:
        return self._request("GET", "/issuetype")

    def list_statuses(self) -> Any:
        return self._request("GET", "/status")

    def list_priorities(self) -> Any:
        return self._request("GET", "/priority")

    def search_assignable_users(
        self,
        query: str = "",
        project_key: Optional[str] = None,
        issue_key: Optional[str] = None,
        max_results: int = 50,
    ) -> Any:
        params: dict[str, Any] = {"maxResults": max_results}
        # Jira's assignable/search uses `query` on Cloud; project/issueKey scope it.
        if query:
            params["query"] = query
        if issue_key:
            params["issueKey"] = issue_key
        elif project_key:
            params["project"] = project_key
        return self._request("GET", "/user/assignable/search", params=params)
