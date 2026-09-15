"""On-premise object data fetch for the SMDG QC Bot (SA-2491).

POSTs to MainService/getObjectOnPremiseData to retrieve the full on-premise data for an
object, given its objectType and its on-premise key (onPremiseObjectID, a JSON string such
as {"objectID":"0001000123"} for a BusinessPartner or {"product":"5001706"} for a
Product/material). Returns a readable message plus the raw response for downstream nodes.

Authentication is the same as the other QC nodes: the credential comes from the flow's
environment (bind the node's "Login credential" field to {{$env.OAUTH}}). Reuses the OAuth
helpers from app.fetch_dupcheck — no dependency on the log-worker.

NOTE: getObjectOnPremiseData is an on-premise-fronting service. Point api_url at a host
that accepts the environment credential (the approuter/web host expects an interactive
user session, so use the direct backend/destination host for a machine credential).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from worker_sdk.layer1_domain.auto_form_mapper import (
    number_field,
    text_field,
    textarea_field,
)

from app.fetch_dupcheck import _oauth_token, _resolve_credential

# Full endpoint; override on the node (e.g. {{$env.MAIN_BASE_API_URL}}/getObjectOnPremiseData).
_DEFAULT_API_URL = "https://<host>/MainService/getObjectOnPremiseData"


def _build_message(object_type: str, opid: str, status: int, body: str) -> str:
    lines = [f"objectType: {object_type}", f"onPremiseObjectID: {opid}"]
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, dict):
        nonempty = {k: v for k, v in data.items() if v not in (None, "", [], {})}
        lines.append(f"HTTP {status} — {len(nonempty)}/{len(data)} top-level field(s) with data")
        preview = json.dumps(data, indent=2, ensure_ascii=False)
        lines.append(preview[:1800] + (" …(truncated)" if len(preview) > 1800 else ""))
    elif isinstance(data, list):
        lines.append(f"HTTP {status} — array with {len(data)} item(s)")
        lines.append(json.dumps(data, ensure_ascii=False)[:1800])
    else:
        lines.append(f"HTTP {status}")
        lines.append((body or "")[:800])
    return "\n".join(lines)


def _fetch(inputs: Optional[dict], parameters: Optional[dict]) -> dict[str, Any]:
    merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}

    object_type = str(merged.get("objectType") or "").strip()
    opid = str(merged.get("onPremiseObjectID") or "").strip()
    if not object_type:
        raise ValueError("'objectType' is required (e.g. Product, BusinessPartner).")
    if not opid:
        raise ValueError("'onPremiseObjectID' is required (a JSON string, e.g. {\"product\":\"5001706\"}).")

    api_url = str(merged.get("api_url") or "").strip() or _DEFAULT_API_URL
    token_url = str(merged.get("token_url") or "").strip()
    flat_raw = str(merged.get("isConvertToFlat", "false")).strip().lower()
    is_flat = flat_raw in ("true", "1", "yes")

    bearer, client_id, client_secret, token_url = _resolve_credential(inputs, token_url)
    if not bearer and not (client_id and client_secret):
        raise RuntimeError(
            "No credential: bind the node's 'Login credential' field to an $env "
            "credential variable (e.g. {{$env.OAUTH}}) from the flow's selected "
            "environment/Section. There is no local .env fallback."
        )

    payload = json.dumps({
        "objectType": object_type,
        "onPremiseObjectID": opid,
        "isConvertToFlat": is_flat,
    }).encode("utf-8")

    start = time.monotonic()
    token = bearer or _oauth_token(token_url, client_id, client_secret)
    req = urllib.request.Request(
        api_url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        status, resp_body = resp.status, resp.read().decode("utf-8", "replace")

    message = _build_message(object_type, opid, status, resp_body)
    return {
        "status": "OK",
        "status_code": status,
        "objectType": object_type,
        "onPremiseObjectID": opid,
        "message": message,
        "response_body": resp_body[:190000],
        "url": api_url,
        "elapsed_ms": (time.monotonic() - start) * 1000,
        "error": "",
    }


def _error_output(msg: str) -> dict[str, Any]:
    return {
        "status": "FAILED", "status_code": 0, "objectType": "", "onPremiseObjectID": "",
        "message": "", "response_body": "", "url": "", "elapsed_ms": 0,
        "error": (msg or "unknown error")[:400],
    }


def make_handler() -> Any:
    """Handler ``(inputs, parameters) -> dict``. Non-fatal: returns a schema-complete
    error payload so the flow keeps flowing (gate downstream on ``outputs.status``)."""
    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            return _fetch(inputs, parameters)
        except urllib.error.HTTPError as exc:
            return _error_output(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the node
            return _error_output(str(exc))
    return _handler


# -- Schemas ------------------------------------------------------------------

INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("objectType", "Object type",
               placeholder="e.g. Product, BusinessPartner"),
    text_field("onPremiseObjectID", "On-premise object ID (JSON string)",
               placeholder='e.g. {"objectID":"0001000123"} or {"product":"5001706"}'),
    text_field("credential_ref", "Login credential",
               placeholder="{{$env.OAUTH}} — a credential variable from the environment"),
    text_field("api_url", "API URL (getObjectOnPremiseData)",
               placeholder="full endpoint, e.g. {{$env.MAIN_BASE_API_URL}}/getObjectOnPremiseData",
               default=_DEFAULT_API_URL),
    text_field("isConvertToFlat", "Convert to flat",
               placeholder="true | false", default="false"),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    text_field("objectType", "Object type", readonly=True),
    text_field("onPremiseObjectID", "On-premise object ID", readonly=True),
    textarea_field("message", "Parsed on-premise data message", readonly=True),
    textarea_field("response_body", "Response Body (JSON)", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
