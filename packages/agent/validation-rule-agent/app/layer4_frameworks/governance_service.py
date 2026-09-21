import os
import re
from typing import Tuple, Optional
import csv
import requests
import pandas as pd
import logging
import io
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter
from typing import List
from app.layer1_domain.entities.template_field import TemplateField


class GovernanceService:
    """Client for interacting with the governance-service."""

    def __init__(self):
        settings = AppSettingsAdapter()
        self.base_url = settings.GOVERNAMENT_API_BASE_URL.rstrip("/")

    def get_template_fields(self, template_id: str) -> List[TemplateField]:
        url = f"{self.base_url}/api/v1/templates/{template_id}/fields"

        response = requests.get(url)

        if response.status_code == 404:
            raise FileNotFoundError(f"Template not found: {template_id!r}")

        if response.status_code >= 400:
            raise RuntimeError(
                f"Governance API request failed "
                f"({response.status_code}): {response.text[:500]}"
            )

        payload = response.json()

        if not payload.get("success", False):
            raise RuntimeError(
                f"Governance API returned unsuccessful response: {payload}"
            )

        return [
            TemplateField(
                table=item["table"],
                field=item["field"],
                alias=item["alias"],
                section=item["section"],
            )
            for item in payload.get("data", [])
        ]
