import json
import logging
from typing import Any, Callable

import requests

from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


logger = logging.getLogger(__name__)


class DataFactoryClient:
    COLUMN_ENDPOINT = "/api/v1/transformation"
    ROW_ENDPOINT = "/api/v1/transformation/rows"

    def __init__(self, settings: AppSettingsAdapter | None = None, timeout: int = 30):
        self._settings = settings or AppSettingsAdapter()
        self._timeout = timeout

    def execute_column_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        import time
        t_api_start = time.time()
        result = self._post(
            endpoint=self.COLUMN_ENDPOINT,
            payload=payload,
            log_prefix="[Column]",
            download_url_getter=lambda result: result.get("result", {})
            .get("odata", {})
            .get("result_file_url"),
        )
        t_api_end = time.time()
        duration = t_api_end - t_api_start
        logger.info(f"[Column] execute_column_payload API call duration: {duration:.2f} seconds")
        return result

    def execute_row_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        import time
        t_api_start = time.time()
        result = self._post(
            endpoint=self.ROW_ENDPOINT,
            payload=payload,
            log_prefix="[Row]",
            download_url_getter=lambda result: result.get("download_url"),
        )
        t_api_end = time.time()
        duration = t_api_end - t_api_start
        logger.info(f"[Row] execute_row_payload API call duration: {duration:.2f} seconds")
        return result

    def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        log_prefix: str,
        download_url_getter: Callable[[dict[str, Any]], str | None],
    ) -> dict[str, Any]:
        api_url = f"{self._settings.DATA_FACTORY_URL}{endpoint}"

        try:
            logger.info("%s Calling API with payload: %s", log_prefix, json.dumps(payload))
            response = requests.post(api_url, json=payload, timeout=self._timeout)

            if response.status_code != 200:
                return {
                    "error": "API error",
                    "status_code": response.status_code,
                    "details": response.text,
                    "payload": payload,
                }

            result = response.json()
            download_url = download_url_getter(result)
            return {
                "message": result.get("message"),
                "download_url": download_url,
                "file_id": result.get("result").get("output_file_id"),
            }

        except Exception as exc:
            return {
                "error": "Request failed",
                "details": str(exc),
                "payload": payload,
            }