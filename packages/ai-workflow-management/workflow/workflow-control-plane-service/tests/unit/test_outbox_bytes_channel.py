"""Phase 3 Sub 3.3 — OutboxBytesChannel."""

from __future__ import annotations

from app.layer4_frameworks.outbox.outbox_bytes_channel import OutboxBytesChannel


class TestPutPop:
    def test_put_then_pop_returns_payload(self):
        ch = OutboxBytesChannel()
        ch.put("ob_1", b"hello")
        assert ch.pop("ob_1") == b"hello"

    def test_pop_consumes(self):
        ch = OutboxBytesChannel()
        ch.put("ob_1", b"hello")
        ch.pop("ob_1")
        assert ch.pop("ob_1") is None

    def test_peek_does_not_consume(self):
        ch = OutboxBytesChannel()
        ch.put("ob_1", b"hello")
        assert ch.peek("ob_1") == b"hello"
        assert ch.pop("ob_1") == b"hello"

    def test_pop_unknown_returns_none(self):
        ch = OutboxBytesChannel()
        assert ch.pop("ob_unknown") is None

    def test_put_overwrites_same_key(self):
        ch = OutboxBytesChannel()
        ch.put("ob_1", b"old")
        ch.put("ob_1", b"new")
        assert ch.pop("ob_1") == b"new"
        assert ch.total_bytes == 0

    def test_put_empty_outbox_id_is_noop(self):
        ch = OutboxBytesChannel()
        ch.put("", b"hello")
        assert len(ch) == 0


class TestEviction:
    def test_evicts_oldest_when_capped(self):
        ch = OutboxBytesChannel(max_bytes=10)
        ch.put("a", b"1234")
        ch.put("b", b"5678")
        ch.put("c", b"9012")  # total 12 > 10 → "a" evicted
        assert ch.pop("a") is None
        assert ch.pop("b") == b"5678"
        assert ch.pop("c") == b"9012"

    def test_total_bytes_tracks(self):
        ch = OutboxBytesChannel()
        ch.put("a", b"1234")
        ch.put("b", b"56")
        assert ch.total_bytes == 6
        ch.pop("a")
        assert ch.total_bytes == 2
