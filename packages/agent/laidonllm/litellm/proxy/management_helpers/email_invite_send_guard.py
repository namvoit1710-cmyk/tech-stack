"""Pure predicate for the send-invite-email server-side guard (SA-2311).

Also holds the send-and-report step (SA-2316): the bookkeeping that used to
live inside user_management_event_hooks.py - counting the enterprise loggers,
recording whether the legacy send succeeded, catching its expected ValueError
- with those dependencies injected so it is unit-testable.

Stdlib-only imports on purpose so it can be loaded and unit-tested without
importing the full litellm package - see invitation_email_guard.py for the
same pattern.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol


class _EmailLogger(Protocol):
    async def send_user_invitation_email(self, *, event: object) -> None: ...


def is_invite_email_silently_undeliverable(send_invite_email: bool, email_was_sent: bool) -> bool:
    """True when the admin asked for an invite email but nothing actually sent one.

    Gate on "did any path (an enterprise BaseEmailLogger callback, or the
    legacy v1 alerting branch) actually send the email" - NOT on "did the
    legacy v1 branch raise". The legacy branch raises ValueError on every
    call when a provider is registered only through
    `general_settings.alerting: ["smtp_email"]`, because it checks for the
    literal "email" key. Treating that raise as the signal would report a
    failure on every working invitation sent via an enterprise logger.
    """
    return send_invite_email and not email_was_sent


def invite_email_was_sent(enterprise_logger_count: int, legacy_send_succeeded: bool) -> bool:
    """True when some path actually handed the invitation to an email logger."""
    return enterprise_logger_count > 0 or legacy_send_succeeded


async def send_invite_emails(
    loggers: list[_EmailLogger],
    legacy_sender: Callable[[], Awaitable[None]],
    event: object,
) -> bool:
    """Send via every registered logger and the legacy sender; report whether
    anything actually sent. Dependencies are injected so this is testable."""
    count = len(loggers)
    for lg in loggers:
        await lg.send_user_invitation_email(event=event)
    legacy_ok = False
    try:
        await legacy_sender()
        legacy_ok = True
    except ValueError:
        # alerting has no "email" entry - expected when an enterprise logger already sent
        pass
    return invite_email_was_sent(count, legacy_ok)
