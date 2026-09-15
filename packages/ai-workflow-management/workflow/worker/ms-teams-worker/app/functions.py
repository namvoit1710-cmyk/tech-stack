"""Dynamic functions exposed by the MS Teams Worker.

These functions can be called at design-time (to test webhook connections)
or at runtime (via _function_call in input_mapping).
"""

from typing import Any, Dict

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth

from app.layer2_application.services.webhook_client import (
    RETIRED_WEBHOOK_MESSAGE,
    TeamsWebhookClient,
    WebhookError,
    WebhookRetiredError,
    webhook_url_from_credential,
)


async def test_webhook(params: Dict[str, Any]) -> Dict[str, Any]:
    """Test webhook connectivity using the node's SELECTED VAULT CREDENTIAL.

    The URL comes only from the `webhook_url` credential the node points at,
    which the control plane resolves into `params.__credentials` before invoking
    (see `_resolve_credentials_into_params`). Same credential, same reader as the
    execute path — so a green test here means a green run, and the user never
    re-types a secret they already saved.

    Previously this read an inline `params["webhook_url"]`, which T5 had already
    removed from the node schema: the test could only pass by pasting the secret
    back into the form, and it proved nothing about the stored credential.

    Returns connection status and latency info.
    """
    try:
        cred = resolve_effective_auth(params, require=True)
    except ValueError as e:
        # Fail closed: no credential selected (or none resolvable) -> no probe.
        return {"success": False, "latency_ms": None, "message": None, "error": str(e)}

    webhook_url = webhook_url_from_credential(cred)

    # Validate required fields
    if not webhook_url:
        return {"success": False, "error": "'webhook_url' is required"}

    if not webhook_url.startswith("https://"):
        return {"success": False, "error": "'webhook_url' must be a valid HTTPS URL"}

    # Retired O365 connector URLs fail fast with the remediation hint —
    # no point sending a probe to a dead service.
    try:
        TeamsWebhookClient.validate_webhook_url(webhook_url)
    except WebhookRetiredError:
        return {
            "success": False,
            "latency_ms": None,
            "message": None,
            "error": RETIRED_WEBHOOK_MESSAGE,
            "error_code": "O365_CONNECTOR_RETIRED",
        }

    try:
        client = TeamsWebhookClient()
        result = client.test_webhook(webhook_url)

        return {
            "success": result.get("success", False),
            "latency_ms": result.get("latency_ms"),
            "message": (
                "Webhook connection test successful"
                if result.get("success")
                else None
            ),
            "error": result.get("error"),
        }

    except WebhookError as e:
        return {
            "success": False,
            "latency_ms": None,
            "message": None,
            "error": str(e),
        }
    except Exception as e:
        return {
            "success": False,
            "latency_ms": None,
            "message": None,
            "error": f"Connection test failed: {str(e)}",
        }


# -- Worker Function Definitions ----------------------------------------------

MS_TEAMS_WORKER_FUNCTIONS = [
    WorkerFunction(
        name="test_webhook",
        description="Test MS Teams Incoming Webhook connectivity by sending a test message to the channel.",
        # No inputs: the URL is a secret and comes from the node's selected
        # credential, never from a form field (T5). Same shape as jira-worker's
        # test_connection, whose input_schema is likewise empty.
        input_schema=[],
        output_schema=[
            {"key": "success", "type": "boolean"},
            {"key": "latency_ms", "type": "number"},
            {"key": "message", "type": "string"},
            {"key": "error", "type": "string"},
        ],
        handler=test_webhook,
    ),
]
