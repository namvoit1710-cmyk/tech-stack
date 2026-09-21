import logging

import httpx

_logger = logging.getLogger(__name__)


FEATURES_API_TIMEOUT_SEC = 30.0
FEATURES_RESPONSE_LOG_PREVIEW_CHARS = 500


class UserPermissionProvider:
    """Returns default user permissions and feature flags."""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        
    def get_user_permissions(self, user_id: str) -> dict:
        return {
            "roles": ["user", "viewer"],
            "allowed_agents": [],
            "subscription_tier": "standard",
            "resource_quotas": {
                "max_requests_per_hour": 100,
            },
        }

    def get_user_features(self, user_email: str, authorization_header: str = "") -> dict:
        if authorization_header.lower().startswith("bearer "):
            features = self._fetch_current_user_features(authorization_header)
            return {
                "email": user_email,
                **features,
            }

        return {}

    def _build_current_user_features_url(self) -> str:
        return f"{self._base_url}/api/v1/features/by-current-user"

    def _fetch_current_user_features(self, authorization_header: str) -> dict:
        request_url = self._build_current_user_features_url()

        try:
            response = httpx.get(
                request_url,
                headers={"Authorization": authorization_header},
                timeout=FEATURES_API_TIMEOUT_SEC,
            )
            _logger.info("Fetched current-user features: %s", response)
            response.raise_for_status()
        except httpx.HTTPError:
            _logger.error(
                "Error fetching current-user features from %s",
                request_url,
                exc_info=True,
            )
            return {}

        payload = self._parse_feature_payload(response, request_url)
        if payload is None:
            return {}

        if isinstance(payload, list):
            return {
                "features": payload,
                "feature_flags": {},
            }

        if isinstance(payload, dict):
            return {
                "features": payload.get("features", []),
                "feature_flags": payload.get("feature_flags", {}),
            }

        _logger.error("Unexpected current-user feature payload from %s: %s", request_url, payload)
        return {}

    def _parse_feature_payload(self, response: httpx.Response, request_url: str):
        if response.status_code == httpx.codes.NO_CONTENT or not response.content:
            _logger.warning("Empty current-user feature response from %s", request_url)
            return None

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            _logger.error(
                "Non-JSON current-user feature response from %s with status %s, content type %s, body preview: %s",
                request_url,
                response.status_code,
                content_type or "<missing>",
                self._build_response_preview(response),
            )
            return None

        try:
            return response.json()
        except ValueError:
            _logger.error(
                "Invalid JSON in current-user feature response from %s",
                request_url,
                exc_info=True,
            )
            return None

    def _build_response_preview(self, response: httpx.Response) -> str:
        try:
            body = response.text.strip()
        except Exception:
            return "<unavailable>"

        if not body:
            return "<empty>"

        if len(body) > FEATURES_RESPONSE_LOG_PREVIEW_CHARS:
            return f"{body[:FEATURES_RESPONSE_LOG_PREVIEW_CHARS]}...(truncated)"

        return body
