"""Domain services for Email worker."""

from .email_client import (
    EmailAuthError,
    EmailError,
    EmailRateLimitError,
    EmailRecipientsRefusedError,
    GraphEmailClient,
    SmtpEmailClient,
)

__all__ = [
    "EmailAuthError",
    "EmailError",
    "EmailRateLimitError",
    "EmailRecipientsRefusedError",
    "GraphEmailClient",
    "SmtpEmailClient",
]
