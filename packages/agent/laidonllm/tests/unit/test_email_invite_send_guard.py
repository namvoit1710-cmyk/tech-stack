"""Tests for litellm/proxy/management_helpers/email_invite_send_guard.py (SA-2311).

Lives outside the litellm package for the same collection-import reason as
test_hana_config.py and test_invitation_email_guard.py: litellm/proxy/ has an
__init__.py, so a test placed there is collected as `litellm.proxy.test_*` and
imports litellm/__init__.py, which does an unguarded `import dotenv` and dies
in this lightweight env. The module under test has no such import (stdlib-only),
so it loads by file path with no stubbing needed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os

import pytest

_TARGET = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "litellm",
    "proxy",
    "management_helpers",
    "email_invite_send_guard.py",
)
_SPEC = importlib.util.spec_from_file_location("email_invite_send_guard", os.path.normpath(_TARGET))
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
is_invite_email_silently_undeliverable = _MOD.is_invite_email_silently_undeliverable
invite_email_was_sent = _MOD.invite_email_was_sent
send_invite_emails = _MOD.send_invite_emails


class _FakeLogger:
    def __init__(self):
        self.calls = []

    async def send_user_invitation_email(self, *, event):
        self.calls.append(event)


def test_no_error_when_send_invite_email_is_false():
    # The admin never asked for an email, so there is nothing to guard against.
    assert is_invite_email_silently_undeliverable(send_invite_email=False, email_was_sent=False) is False
    assert is_invite_email_silently_undeliverable(send_invite_email=False, email_was_sent=True) is False


def test_no_error_when_an_email_was_actually_sent():
    # e.g. an enterprise BaseEmailLogger (smtp_email, resend, sendgrid) sent it.
    assert is_invite_email_silently_undeliverable(send_invite_email=True, email_was_sent=True) is False


def test_error_when_requested_but_nothing_sent_it():
    # No logger registered at all - the checkbox lied to the admin.
    assert is_invite_email_silently_undeliverable(send_invite_email=True, email_was_sent=False) is True


@pytest.mark.parametrize("email_was_sent", [True, False])
def test_gate_is_on_email_was_sent_not_on_legacy_branch_raising(email_was_sent):
    """The trap from the ticket.

    When general_settings.alerting: ["smtp_email"] is configured, the legacy v1
    branch still raises ValueError (it only checks for the literal "email" key)
    even though the enterprise smtp_email logger already sent the invitation.
    The guard must be driven purely by whether an email was actually sent, so a
    caller that caught that ValueError and correctly recorded email_was_sent=True
    must NOT trigger the guard.
    """
    result = is_invite_email_silently_undeliverable(send_invite_email=True, email_was_sent=email_was_sent)
    assert result is (not email_was_sent)


def test_no_send_when_neither_branch_sent():
    assert invite_email_was_sent(enterprise_logger_count=0, legacy_send_succeeded=False) is False


def test_sent_when_only_enterprise_logger_sent():
    # Catches a regression where the enterprise branch stops recording that it sent.
    assert invite_email_was_sent(enterprise_logger_count=1, legacy_send_succeeded=False) is True


def test_sent_when_only_legacy_branch_sent():
    # Catches a regression where the legacy branch stops recording that it sent.
    assert invite_email_was_sent(enterprise_logger_count=0, legacy_send_succeeded=True) is True


def test_sent_when_both_branches_sent():
    assert invite_email_was_sent(enterprise_logger_count=1, legacy_send_succeeded=True) is True


# ---------------------------------------------------------------------------
# send_invite_emails (SA-2316) - the send-and-report step extracted out of
# user_management_event_hooks.py, dependencies injected so it is reachable by
# these tests. Each test below is named after the mutation from the ticket's
# Done-when that it must fail on.
# ---------------------------------------------------------------------------


def test_sent_when_only_enterprise_branch_actually_sends():
    """Catches the enterprise branch no longer recording that it sent
    (e.g. the logger count forced to 0, or the result driven only by whether
    the legacy branch raised): the legacy branch raises here, so only the
    enterprise send can make this True.
    """
    logger = _FakeLogger()

    async def legacy_sender():
        raise ValueError("no email alerting configured")

    result = asyncio.run(send_invite_emails(loggers=[logger], legacy_sender=legacy_sender, event="evt"))

    assert result is True
    assert logger.calls == ["evt"]


def test_sent_when_only_legacy_branch_actually_sends():
    """Catches the legacy branch no longer recording that it succeeded."""

    async def legacy_sender():
        return None

    result = asyncio.run(send_invite_emails(loggers=[], legacy_sender=legacy_sender, event="evt"))

    assert result is True


def test_not_sent_when_neither_branch_sends():
    async def legacy_sender():
        raise ValueError("no email alerting configured")

    result = asyncio.run(send_invite_emails(loggers=[], legacy_sender=legacy_sender, event="evt"))

    assert result is False


def test_unexpected_legacy_error_is_not_swallowed():
    # Only the documented ValueError ("alerting has no 'email' entry") is expected;
    # anything else must propagate rather than being reported as "not sent".
    async def legacy_sender():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(send_invite_emails(loggers=[], legacy_sender=legacy_sender, event="evt"))
