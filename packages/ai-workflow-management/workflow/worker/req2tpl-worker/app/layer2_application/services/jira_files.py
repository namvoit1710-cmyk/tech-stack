"""Jira attachment transport for the req2tpl worker.

The only two network paths in this worker: pull a ticket's workbooks down to the
run directory, and push the produced template back up as an attachment.

Deliberately self-contained rather than importing jira-worker's ``JiraClient``:
these are separate deployables and a shared import across worker directories
would make one worker's release able to break another's. The credential *shape*
is the same on purpose — one vault ``jira-api-token`` covers both.

Why the bytes stop here: an .xlsx referral is ~180 KB, which base64s past the
256 KB TASK-input ceiling once it rides the run envelope alongside the other two
workbooks, and a split input is not re-joined today. So the file content never
enters the workflow — only its path, size and sha256 do.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

_API_ROOT = "/rest/api/3"
_DEFAULT_TIMEOUT = 60.0
_CHUNK = 64 * 1024


class JiraFilesError(Exception):
    """A Jira transport or credential failure."""

    def __init__(self, message: str, error_code: str = "JIRA_ERROR",
                 status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class JiraFilesClient:
    """Attachment list / download / upload against Jira Cloud REST v3."""

    def __init__(self, base_url: str, auth: Optional[tuple[str, str]] = None,
                 bearer: Optional[str] = None, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._base = base_url.rstrip("/")
        self._auth = auth
        self._bearer = bearer
        self._timeout = timeout

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_credential(cls, cred: Optional[Dict[str, Any]],
                        timeout: float = _DEFAULT_TIMEOUT) -> "JiraFilesClient":
        """Build from a vault-resolved ``{type, config}`` credential.

        The credential is the single source of truth — it carries the site URL as
        well as the secret, so a node cannot be pointed at one site while
        authenticating for another.
        """
        if not isinstance(cred, dict):
            raise JiraFilesError(
                "a Jira credential is required: set a credential_ref on the node",
                error_code="AUTH_ERROR",
            )
        cred_type = cred.get("type")
        config = cred.get("config") or {}

        base_url = str(config.get("domain") or config.get("baseUrl") or "").strip()
        if not base_url:
            raise JiraFilesError(
                "the Jira credential is missing the site URL (domain)",
                error_code="AUTH_ERROR",
            )
        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url

        if cred_type in ("json", "basic"):
            email = str(config.get("email") or config.get("username") or "").strip()
            token = str(config.get("apiToken") or config.get("password") or "").strip()
            if email and token:
                return cls(base_url, auth=(email, token), timeout=timeout)
            if token:
                return cls(base_url, bearer=token, timeout=timeout)
        if cred_type == "bearer" and config.get("token"):
            return cls(base_url, bearer=str(config["token"]), timeout=timeout)
        if cred_type == "api_key" and config.get("value"):
            return cls(base_url, bearer=str(config["value"]), timeout=timeout)

        raise JiraFilesError(
            f"credential type '{cred_type}' is not usable for Jira "
            "(use a Jira API Token credential: site domain + email + API token)",
            error_code="AUTH_ERROR",
        )

    @property
    def base_url(self) -> str:
        return self._base

    def browse_url(self, issue_key: str) -> str:
        return f"{self._base}/browse/{issue_key}"

    # -- attachments ----------------------------------------------------------

    def list_attachments(self, issue_key: str) -> List[Dict[str, Any]]:
        """Attachment metadata for one issue.

        ``GET /issue/{key}?fields=attachment`` is metadata only — the bytes come
        from a second call to the per-attachment ``content`` URL.
        """
        data = self._request("GET", f"/issue/{issue_key}", params={"fields": "attachment"})
        raw = ((data or {}).get("fields") or {}).get("attachment") or []
        out: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            out.append({
                "id": str(item.get("id") or ""),
                "filename": str(item.get("filename") or ""),
                "size": int(item.get("size") or 0),
                "mime_type": str(item.get("mimeType") or ""),
                "created": str(item.get("created") or ""),
                "content_url": str(item.get("content") or ""),
                "author": str(((item.get("author") or {}).get("displayName")) or ""),
            })
        return out

    def download_to(self, content_url: str, dest: Path, *, max_bytes: int) -> Dict[str, Any]:
        """Stream one attachment to ``dest``. Returns size + sha256.

        ``content_url`` comes back inside issue JSON, which is user-editable
        territory, so it is checked to be on the credential's own host before any
        Authorization header is attached — otherwise a crafted issue could aim
        this worker's Jira secret at a host of the attacker's choosing. The size
        cap is enforced *while* streaming, so an oversized file never lands.

        Two things this endpoint demands, both learned from a live 406:

        * ``Accept: */*``. Asking for ``application/octet-stream`` gets a **406
          Not Acceptable** — Jira negotiates the attachment's real content type,
          which for an .xlsx is the OOXML spreadsheet type, not octet-stream.
        * Redirects are followed **without credentials**. Jira answers with a 3xx
          to a media host and the redirect URL carries its own signed token, so
          re-sending Basic auth there would hand this worker's Jira password to a
          different host for no benefit.
        """
        self._assert_same_origin(content_url)
        headers = {"Accept": "*/*"}
        if self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"

        digest = hashlib.sha256()
        written = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            # Hop 1: authenticated, no auto-follow, so credentials cannot ride a
            # redirect off this host.
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                resp = client.request("GET", content_url, headers=headers,
                                      auth=self._auth if self._auth else None)
                url, req_headers, auth = content_url, headers, self._auth
                if resp.is_redirect:
                    url = str(resp.next_request.url) if resp.next_request else \
                        str(httpx.URL(resp.headers.get("location", "")))
                    req_headers, auth = {"Accept": "*/*"}, None  # signed URL; no creds
                    resp.close()
                else:
                    resp.close()

            with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                with client.stream("GET", url, headers=req_headers, auth=auth) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise JiraFilesError(
                            f"Jira attachment download failed: HTTP {resp.status_code}",
                            error_code=_error_code(resp.status_code),
                            status_code=resp.status_code,
                        )
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes(_CHUNK):
                            written += len(chunk)
                            if written > max_bytes:
                                raise JiraFilesError(
                                    f"attachment exceeds REQ2TPL_MAX_ATTACHMENT_BYTES "
                                    f"({max_bytes} bytes) - refused",
                                    error_code="VALIDATION_ERROR",
                                )
                            digest.update(chunk)
                            fh.write(chunk)
            tmp.replace(dest)
        except httpx.TimeoutException as exc:
            raise JiraFilesError(f"Jira download timed out: {exc}", error_code="TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise JiraFilesError(f"Jira download failed: {exc}", error_code="NETWORK_ERROR") from exc
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        return {"bytes": written, "sha256": digest.hexdigest()}

    def add_attachment(self, issue_key: str, filename: str, content: bytes,
                       content_type: str = "application/octet-stream") -> Any:
        """Upload one file to an issue. Needs the XSRF opt-out header."""
        files = {"file": (filename, content, content_type)}
        return self._request(
            "POST",
            f"/issue/{issue_key}/attachments",
            files=files,
            extra_headers={"X-Atlassian-Token": "no-check"},
        )

    def add_comment(self, issue_key: str, text: str) -> Any:
        """Add a plain-text comment (wrapped as ADF, one paragraph per line)."""
        content: List[Dict[str, Any]] = []
        for line in str(text).split("\n"):
            if line:
                content.append({"type": "paragraph",
                                "content": [{"type": "text", "text": line}]})
            else:
                content.append({"type": "paragraph"})
        body = {"type": "doc", "version": 1, "content": content or [{"type": "paragraph"}]}
        return self._request("POST", f"/issue/{issue_key}/comment", json_body={"body": body})

    # -- transport ------------------------------------------------------------

    def _assert_same_origin(self, url: str) -> None:
        want, got = urlparse(self._base), urlparse(url)
        if not got.scheme or not got.netloc:
            raise JiraFilesError(
                f"attachment content URL is not absolute: {url!r}",
                error_code="VALIDATION_ERROR",
            )
        if (got.scheme, got.netloc.lower()) != (want.scheme, want.netloc.lower()):
            raise JiraFilesError(
                f"attachment content URL points at {got.scheme}://{got.netloc}, not the "
                f"credential's site {want.scheme}://{want.netloc} — refused",
                error_code="VALIDATION_ERROR",
            )

    def _request(self, method: str, path: str, *, params: Optional[dict] = None,
                 json_body: Optional[Any] = None, files: Optional[Any] = None,
                 extra_headers: Optional[dict] = None) -> Any:
        url = f"{self._base}{path}" if path.startswith("/rest/") else f"{self._base}{_API_ROOT}{path}"
        headers: Dict[str, str] = {"Accept": "application/json"}
        if self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"
        if extra_headers:
            headers.update(extra_headers)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.request(method, url, params=params, json=json_body,
                                      files=files, headers=headers,
                                      auth=self._auth if self._auth else None)
        except httpx.TimeoutException as exc:
            raise JiraFilesError(f"Jira request timed out: {exc}", error_code="TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise JiraFilesError(f"Jira request failed: {exc}", error_code="NETWORK_ERROR") from exc

        if resp.status_code >= 400:
            raise JiraFilesError(_error_message(resp), error_code=_error_code(resp.status_code),
                                 status_code=resp.status_code)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None


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


def _error_message(resp: httpx.Response) -> str:
    prefix = f"Jira API {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        text = resp.text.strip()
        return f"{prefix}: {text[:300]}" if text else prefix
    parts: List[str] = []
    if isinstance(data, dict):
        for msg in data.get("errorMessages") or []:
            parts.append(str(msg))
        errors = data.get("errors")
        if isinstance(errors, dict):
            parts.extend(f"{k}: {v}" for k, v in errors.items())
    return f"{prefix}: {'; '.join(parts)}" if parts else prefix
