"""Outbox error taxonomy — Phase 3 Sub 3.2.

Each handler in ``layer4_frameworks/outbox/handlers/`` raises one of
``RetryableError`` (transient — dispatcher retries with backoff) or
``PermanentError`` (won't help to retry — dispatcher records
``OutboxResolved(outcome="failed")`` immediately).

Mapping guidance lives in
``docs/design/phase-3-executor-extraction.md`` §7.3:
  - Retryable: network timeouts, 5xx from the file service,
    Kafka enqueue failures.
  - Permanent: 4xx (especially 404/409/422), payload validation
    errors.
"""

from __future__ import annotations


class OutboxError(Exception):
    """Base class — never raised directly; subclasses signal intent."""


class RetryableError(OutboxError):
    """Transient failure — the dispatcher retries with backoff."""


class PermanentError(OutboxError):
    """Non-recoverable failure — the dispatcher gives up immediately."""
