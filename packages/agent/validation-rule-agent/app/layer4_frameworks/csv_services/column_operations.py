import logging
import requests
from typing import List, Dict
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


class ColumnTransformationExecutor:
    """
    Service for executing column validation via the Data Factory API.
    """

    def __init__(self):
        settings = AppSettingsAdapter()
        self.base_url = settings.DATA_FACTORY_URL
        self.logger = logging.getLogger(self.__class__.__name__)

    def execute_column_operations(
        self,
        file_path: str,
        file_format: str,
        rules: List[Dict]
    ) -> str:
        """
        Execute a single set of column validation operations.
        Returns the message.
        """
        url = f"{self.base_url}/api/v1/validation"
        file_format = file_format.lstrip('.') if file_format else file_format
        payload = {
            "file_path": file_path,
            "file_format": file_format,
            "rules": rules
        }

        self.logger.info("Column payload: %s", payload)

        headers = {
            "accept": "application/json",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(url, headers=headers, json=payload)

            if response.status_code == 422:
                try:
                    error_data = response.json()
                    raise ValueError(f"Invalid request payload: {error_data}")
                except ValueError:
                    raise ValueError("Invalid JSON payload sent to column validation service")

            if response.status_code >= 500:
                try:
                    error_data = response.json()
                    detail = error_data.get("detail", "Internal server error")
                    raise RuntimeError(f"Column validation service error: {detail}")
                except Exception:
                    raise RuntimeError("Column validation service internal error")

            response.raise_for_status()
            data = response.json()

            return data
            
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to call column transformation service: {str(e)}")

    def execute_batch(
        self,
        current_file: str,
        file_format: str,
        steps: List[Dict]
    ) -> str:
        """
        Execute multiple column transformation steps sequentially.
        Returns the final file reference.
        """
        rules = []
        for step in steps:
            self.logger.info(
                "Step=execute_column_operation operation=%s", step["type"]
            )
            rules.append(step)
            result = self.execute_column_operations(
                file_path=current_file,
                file_format=file_format,
                rules=rules
            )


        return result
