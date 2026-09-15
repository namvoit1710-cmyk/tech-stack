"""Dynamic functions exposed by the HTTP Request Worker.

These functions can be called at design-time (to populate UI forms)
or at runtime (via _function_call in input_mapping).
"""

from typing import Any

import httpx

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction


async def test_connection(params: dict[str, Any]) -> dict[str, Any]:
    """Test connectivity to a URL. Returns status code and latency."""
    url = params.get("url", "")
    method = params.get("method", "HEAD").upper()
    timeout = params.get("timeout_seconds", 5)
    if not url:
        return {"reachable": False, "error": "url is required"}
    try:
        async with httpx.AsyncClient(timeout=float(timeout)) as client:
            response = await client.request(method, url)
            return {
                "reachable": True,
                "status_code": response.status_code,
                "latency_ms": response.elapsed.total_seconds() * 1000,
                "content_type": response.headers.get("content-type", ""),
            }
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


async def extract_response_keys(params: dict[str, Any]) -> dict[str, Any]:
    """Fetch a URL and return the top-level keys of the JSON response.

    Useful for design-time: lets the user see what fields are available
    from an API endpoint so they can configure output_mapping.
    """
    url = params.get("url", "")
    method = params.get("method", "GET").upper()
    headers = params.get("headers", {})
    if not url:
        return {"keys": [], "error": "url is required"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, headers=headers)
            data = response.json()
            if isinstance(data, dict):
                keys = list(data.keys())
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                keys = list(data[0].keys())
            else:
                keys = []
            return {
                "keys": keys,
                "status_code": response.status_code,
                "is_array": isinstance(data, list),
                "sample_count": len(data) if isinstance(data, list) else 1,
            }
    except Exception as exc:
        return {"keys": [], "error": str(exc)}


HTTP_WORKER_FUNCTIONS = [
    WorkerFunction(
        name="test_connection",
        description="Test connectivity to a URL. Returns reachability, status code, and latency.",
        input_schema=[
            {"key": "url", "type": "string", "required": True, "label": "URL"},
            {"key": "method", "type": "string", "required": False, "label": "Method", "default": "HEAD"},
            {"key": "timeout_seconds", "type": "number", "required": False, "label": "Timeout (s)", "default": 5},
        ],
        output_schema=[
            {"key": "reachable", "type": "boolean"},
            {"key": "status_code", "type": "number"},
            {"key": "latency_ms", "type": "number"},
            {"key": "content_type", "type": "string"},
            {"key": "error", "type": "string"},
        ],
        handler=test_connection,
    ),
    WorkerFunction(
        name="extract_response_keys",
        description="Fetch a URL and return the top-level JSON keys. Useful for configuring output mappings.",
        input_schema=[
            {"key": "url", "type": "string", "required": True, "label": "URL"},
            {"key": "method", "type": "string", "required": False, "label": "Method", "default": "GET"},
            {"key": "headers", "type": "object", "required": False, "label": "Headers"},
        ],
        output_schema=[
            {"key": "keys", "type": "array"},
            {"key": "status_code", "type": "number"},
            {"key": "is_array", "type": "boolean"},
            {"key": "sample_count", "type": "number"},
            {"key": "error", "type": "string"},
        ],
        handler=extract_response_keys,
    ),
]
