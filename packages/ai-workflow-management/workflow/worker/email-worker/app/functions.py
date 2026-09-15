"""Dynamic functions exposed by the Email Worker.

These functions can be called at design-time (to test SMTP / Graph connectivity)
or at runtime (via _function_call in input_mapping).
"""

from typing import Any, Dict

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction

from app.layer2_application.services.email_client import (
    EmailError,
    GraphEmailClient,
    SmtpEmailClient,
)
from app.layer4_frameworks.config import settings


async def test_smtp_connection(params: Dict[str, Any]) -> Dict[str, Any]:
    """Test SMTP relay connectivity (and login when a username is given).

    Returns connection status and latency info.
    """
    host = (params.get("host") or "").strip()
    if not host:
        return {"success": False, "error": "'host' is required"}

    security = (params.get("security") or "starttls").strip().lower()
    if security not in ("starttls", "ssl", "none"):
        return {
            "success": False,
            "error": "'security' must be one of ['starttls', 'ssl', 'none']",
        }

    raw_port = params.get("port")
    if raw_port in (None, ""):
        port = 465 if security == "ssl" else 587
    else:
        try:
            port = int(str(raw_port).strip())
        except ValueError:
            return {"success": False, "error": "'port' must be a number"}

    try:
        client = SmtpEmailClient()
        result = client.test_connection(
            host=host,
            port=port,
            security=security,
            username=(params.get("username") or "").strip(),
            password=params.get("password") or "",
        )

        return {
            "success": result.get("success", False),
            "latency_ms": result.get("latency_ms"),
            "message": (
                "SMTP connection test successful" if result.get("success") else None
            ),
            "error": result.get("error"),
        }

    except Exception as e:
        return {
            "success": False,
            "latency_ms": None,
            "message": None,
            "error": f"Connection test failed: {str(e)}",
        }


async def test_graph_credential(params: Dict[str, Any]) -> Dict[str, Any]:
    """Test Microsoft Graph OAuth2 app credentials by fetching an access token.

    Returns token acquisition status and latency info.
    """
    token_url = (params.get("token_url") or "").strip()
    client_id = (params.get("client_id") or "").strip()
    client_secret = params.get("client_secret") or ""

    if not token_url:
        return {"success": False, "error": "'token_url' is required"}
    if not token_url.startswith("https://"):
        return {"success": False, "error": "'token_url' must be a valid HTTPS URL"}
    if not client_id:
        return {"success": False, "error": "'client_id' is required"}
    if not client_secret:
        return {"success": False, "error": "'client_secret' is required"}

    import time

    start = time.monotonic()
    try:
        client = GraphEmailClient(graph_base_url=settings.GRAPH_BASE_URL)
        client.fetch_token(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scopes=params.get("scopes") or "",
        )
        latency_ms = (time.monotonic() - start) * 1000

        return {
            "success": True,
            "latency_ms": round(latency_ms, 2),
            "message": "OAuth2 token acquired successfully",
            "error": None,
        }

    except EmailError as e:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "latency_ms": round(latency_ms, 2),
            "message": None,
            "error": str(e),
        }
    except Exception as e:
        return {
            "success": False,
            "latency_ms": None,
            "message": None,
            "error": f"Token test failed: {str(e)}",
        }


# -- Worker Function Definitions ----------------------------------------------

EMAIL_WORKER_FUNCTIONS = [
    WorkerFunction(
        name="test_smtp_connection",
        description="Test SMTP relay connectivity (connect, TLS, and login when a username is given).",
        input_schema=[
            {
                "key": "host",
                "type": "string",
                "required": True,
                "label": "SMTP Host",
                "placeholder": "smtp.office365.com",
            },
            {
                "key": "port",
                "type": "number",
                "required": False,
                "label": "Port",
                "placeholder": "587",
            },
            {
                "key": "security",
                "type": "string",
                "required": False,
                "label": "Security (starttls/ssl/none)",
                "placeholder": "starttls",
            },
            {
                "key": "username",
                "type": "string",
                "required": False,
                "label": "Username",
                "placeholder": "noreply@company.com",
            },
            {
                "key": "password",
                "type": "string",
                "required": False,
                "label": "Password",
                "inputType": "password",
            },
        ],
        output_schema=[
            {"key": "success", "type": "boolean"},
            {"key": "latency_ms", "type": "number"},
            {"key": "message", "type": "string"},
            {"key": "error", "type": "string"},
        ],
        handler=test_smtp_connection,
    ),
    WorkerFunction(
        name="test_graph_credential",
        description="Test Microsoft Graph OAuth2 application credentials by running the client-credentials grant.",
        input_schema=[
            {
                "key": "token_url",
                "type": "string",
                "required": True,
                "label": "Token URL",
                "placeholder": "https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token",
            },
            {
                "key": "client_id",
                "type": "string",
                "required": True,
                "label": "Client ID",
            },
            {
                "key": "client_secret",
                "type": "string",
                "required": True,
                "label": "Client Secret",
                "inputType": "password",
            },
            {
                "key": "scopes",
                "type": "string",
                "required": False,
                "label": "Scopes",
                "placeholder": "https://graph.microsoft.com/.default",
            },
        ],
        output_schema=[
            {"key": "success", "type": "boolean"},
            {"key": "latency_ms", "type": "number"},
            {"key": "message", "type": "string"},
            {"key": "error", "type": "string"},
        ],
        handler=test_graph_credential,
    ),
]
