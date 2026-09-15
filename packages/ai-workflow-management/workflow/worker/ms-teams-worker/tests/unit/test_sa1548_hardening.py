"""SA-1548 — Workflows-webhook hardening + @mention entities.

Covers: retired O365 connector URL fail-fast (client, use case, design-time
function) and mention entities in Adaptive Cards (builder, use case, schema).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.functions import test_webhook as probe_webhook  # alias: avoid pytest collection
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    MS_TEAMS_INPUT_SCHEMA,
    ExecuteTaskUseCase,
)
from app.layer2_application.services.webhook_client import (
    RETIRED_WEBHOOK_MESSAGE,
    TeamsWebhookClient,
    WebhookRetiredError,
)
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)


WORKFLOWS_URL = "https://prod-42.westus.logic.azure.com/workflows/ok"
RETIRED_B2_URL = "https://contoso.webhook.office.com/webhookb2/abc/IncomingWebhook/def"
RETIRED_LEGACY_URL = "https://outlook.office.com/webhook/abc-def"


def _cred_inputs(url, **extra):
    return {
        "credential_ref": "cred-1",
        "__credentials": {"cred-1": {"type": "webhook_url", "config": {"url": url}}},
        **extra,
    }


class TestValidateWebhookUrl:
    def test_workflows_url_passes(self):
        TeamsWebhookClient.validate_webhook_url(WORKFLOWS_URL)  # no raise

    @pytest.mark.parametrize("url", [RETIRED_B2_URL, RETIRED_LEGACY_URL])
    def test_retired_urls_raise(self, url):
        with pytest.raises(WebhookRetiredError) as exc:
            TeamsWebhookClient.validate_webhook_url(url)
        assert exc.value.error_code == "O365_CONNECTOR_RETIRED"
        assert "Workflows" in str(exc.value)

    def test_outlook_host_without_webhook_path_passes(self):
        TeamsWebhookClient.validate_webhook_url("https://outlook.office.com/mail")

    def test_empty_url_is_a_noop(self):
        TeamsWebhookClient.validate_webhook_url("")

    def test_send_fails_fast_without_http_call(self):
        client = TeamsWebhookClient()
        with patch.object(httpx.Client, "post") as mock_post:
            with pytest.raises(WebhookRetiredError):
                client.send_message(webhook_url=RETIRED_B2_URL, content="hi")
        mock_post.assert_not_called()


class TestBuildAdaptiveCardMentions:
    def test_mentions_add_entities_and_textblock(self):
        card = TeamsWebhookClient.build_adaptive_card(
            title="T", body="B",
            mentions=[{"name": "Jane Steward", "id": "jane@contoso.com"}],
        )
        assert card["msteams"]["entities"] == [
            {
                "type": "mention",
                "text": "<at>Jane Steward</at>",
                "mentioned": {"id": "jane@contoso.com", "name": "Jane Steward"},
            }
        ]
        assert card["body"][-1]["text"] == "<at>Jane Steward</at>"

    def test_invalid_mention_rows_are_dropped(self):
        card = TeamsWebhookClient.build_adaptive_card(
            title="T", body="B",
            mentions=[{"name": "NoId"}, {"id": "no-name@x.com"}, "junk"],
        )
        assert "msteams" not in card
        assert len(card["body"]) == 2  # no mention TextBlock appended

    def test_no_mentions_keeps_card_shape(self):
        card = TeamsWebhookClient.build_adaptive_card(title="T", body="B")
        assert "msteams" not in card
        assert len(card["body"]) == 2


class TestExecuteTaskRetiredUrl:
    @pytest.fixture
    def use_case(self):
        return ExecuteTaskUseCase(logger=MagicMock(), monitor=MagicMock())

    @pytest.mark.parametrize("url", [RETIRED_B2_URL, RETIRED_LEGACY_URL])
    def test_execute_returns_retired_error_code(self, use_case, url):
        cmd = ExecuteTaskCommand(
            task_id="t1", action="execute",
            inputs=_cred_inputs(url, operation_type="send_message", message_content="x"),
            parameters={},
        )
        result = use_case.execute(cmd)
        assert result.status.value == "error"
        assert result.outputs["error_code"] == "O365_CONNECTOR_RETIRED"
        assert "Workflows" in result.outputs["error"]

    def test_execute_card_passes_mentions_to_payload(self, use_case):
        captured = {}

        def fake_send(webhook_url, card_payload):
            captured["payload"] = card_payload
            return {"success": True, "response": "1"}

        with patch.object(TeamsWebhookClient, "send_adaptive_card", side_effect=fake_send):
            cmd = ExecuteTaskCommand(
                task_id="t2", action="execute",
                inputs=_cred_inputs(
                    WORKFLOWS_URL,
                    operation_type="send_adaptive_card",
                    card_title="Approve",
                    card_body="Please review",
                    mentions=[{"name": "Jane", "id": "jane@contoso.com"}],
                ),
                parameters={},
            )
            result = use_case.execute(cmd)

        assert result.status.value == "success"
        entities = captured["payload"]["msteams"]["entities"]
        assert entities[0]["mentioned"]["id"] == "jane@contoso.com"

    def test_execute_card_tolerates_mentions_json_string(self, use_case):
        captured = {}

        def fake_send(webhook_url, card_payload):
            captured["payload"] = card_payload
            return {"success": True, "response": "1"}

        with patch.object(TeamsWebhookClient, "send_adaptive_card", side_effect=fake_send):
            cmd = ExecuteTaskCommand(
                task_id="t3", action="execute",
                inputs=_cred_inputs(
                    WORKFLOWS_URL,
                    operation_type="send_adaptive_card",
                    card_title="Approve",
                    card_body="Please review",
                    mentions='[{"name": "Jane", "id": "jane@contoso.com"}]',
                ),
                parameters={},
            )
            result = use_case.execute(cmd)

        assert result.status.value == "success"
        assert "msteams" in captured["payload"]

    def test_execute_card_bad_mentions_string_ignored(self, use_case):
        captured = {}

        def fake_send(webhook_url, card_payload):
            captured["payload"] = card_payload
            return {"success": True, "response": "1"}

        with patch.object(TeamsWebhookClient, "send_adaptive_card", side_effect=fake_send):
            cmd = ExecuteTaskCommand(
                task_id="t4", action="execute",
                inputs=_cred_inputs(
                    WORKFLOWS_URL,
                    operation_type="send_adaptive_card",
                    card_title="Approve",
                    card_body="Please review",
                    mentions="{not json",
                ),
                parameters={},
            )
            result = use_case.execute(cmd)

        assert result.status.value == "success"
        assert "msteams" not in captured["payload"]


class TestSchemaMentionsField:
    def test_mentions_field_present_with_card_condition(self):
        field = next(f for f in MS_TEAMS_INPUT_SCHEMA if f["key"] == "mentions")
        cond = field["fieldConfig"]["condition"]
        assert cond["conditionValue"] == "send_adaptive_card"
        item_fields = field["fields"][0]["fields"]
        assert {f["key"] for f in item_fields} == {"name", "id"}


class TestDesignTimeFunctionRetiredUrl:
    @pytest.mark.asyncio
    async def test_flags_retired_url_without_probe(self):
        # The URL reaches the design-time probe the same way it reaches a run:
        # from the node's vault credential, never an inline param.
        with patch.object(httpx.Client, "post") as mock_post:
            result = await probe_webhook(_cred_inputs(RETIRED_B2_URL))
        mock_post.assert_not_called()
        assert result["success"] is False
        assert result["error_code"] == "O365_CONNECTOR_RETIRED"
        assert result["error"] == RETIRED_WEBHOOK_MESSAGE
