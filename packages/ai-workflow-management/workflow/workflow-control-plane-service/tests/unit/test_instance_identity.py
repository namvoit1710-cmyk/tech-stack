"""Instance identity — the input that decides how many broker queues a
deployment burns over its lifetime.

The response-topic queue is named ``sub-orchestration-responses-<sha256(id)[:8]>``,
so a random id costs one queue per BOOT and a stable id one per REPLICA. These
tests pin the three properties that matter: stable across restarts, distinct
across replicas, and never derived from an unexpanded manifest placeholder.
"""
from __future__ import annotations

import json

import pytest

from app.layer4_frameworks.config.instance_identity import (
    cf_instance_identity,
    resolve_instance_id,
)
from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
    response_topic_for,
)

APP_NAME = "ai-workflow-control-plane"


def _cf_env(monkeypatch, *, index: str, app_name: str = APP_NAME) -> None:
    """Put the process in the env a CF container actually gets."""
    monkeypatch.setenv("CF_INSTANCE_INDEX", index)
    monkeypatch.setenv(
        "VCAP_APPLICATION",
        json.dumps({"application_name": app_name, "space_name": "tenant-1"}),
    )


def _no_cf(monkeypatch) -> None:
    monkeypatch.delenv("CF_INSTANCE_INDEX", raising=False)
    monkeypatch.delenv("VCAP_APPLICATION", raising=False)


class TestExplicitConfiguration:
    """An explicitly configured INSTANCE_ID stays authoritative — test
    harnesses that run two bootstraps against one broker stub depend on it."""

    def test_configured_value_wins_over_the_cf_env(self, monkeypatch):
        _cf_env(monkeypatch, index="0")
        assert resolve_instance_id("harness-a") == "harness-a"

    def test_surrounding_whitespace_is_trimmed(self, monkeypatch):
        _no_cf(monkeypatch)
        assert resolve_instance_id("  harness-a  ") == "harness-a"

    def test_blank_configured_value_is_not_an_identity(self, monkeypatch):
        _cf_env(monkeypatch, index="3")
        assert resolve_instance_id("   ") == f"{APP_NAME}_3"


class TestUnexpandedPlaceholder:
    """The defect this ticket exists for.

    ``deployment-executor.yml`` USED TO pin
    ``INSTANCE_ID: wio-executor_${CF_INSTANCE_INDEX}``. That manifest has since
    been deleted (SA-2272 — nothing built it and no CF space ran it), but the
    defect it demonstrated is not manifest-specific and the guard stays: Cloud
    Foundry does not interpolate ``${...}`` in manifest env values, the deploy
    pipeline writes it verbatim and the app reads it with ``os.getenv``, so the
    process sees the LITERAL — identical on every replica. Honouring it
    collapses the deployment onto one response topic, which is the exact
    collision SA-2087 set out to remove. Any future manifest can reintroduce
    it, which is why this is tested against the resolver rather than against a
    file.
    """

    LITERAL = "wio-executor_${CF_INSTANCE_INDEX}"

    def test_placeholder_is_rejected_in_favour_of_the_real_env(self, monkeypatch):
        _cf_env(monkeypatch, index="2", app_name="wio-executor")
        assert resolve_instance_id(self.LITERAL) == "wio-executor_2"

    def test_replicas_do_not_collide_despite_the_shared_literal(self, monkeypatch):
        _cf_env(monkeypatch, index="0", app_name="wio-executor")
        first = response_topic_for(resolve_instance_id(self.LITERAL))
        _cf_env(monkeypatch, index="1", app_name="wio-executor")
        second = response_topic_for(resolve_instance_id(self.LITERAL))
        assert first != second

    def test_placeholder_off_cloud_foundry_falls_back_not_through(self, monkeypatch):
        """Off CF there is nothing to derive from, but the literal must still
        never be adopted — two local processes would share one topic."""
        _no_cf(monkeypatch)
        first = resolve_instance_id(self.LITERAL)
        second = resolve_instance_id(self.LITERAL)
        assert first != self.LITERAL
        assert first != second


class TestCfDerivation:
    def test_identity_is_app_qualified_and_index_bound(self, monkeypatch):
        _cf_env(monkeypatch, index="4")
        assert cf_instance_identity() == f"{APP_NAME}_4"

    def test_stable_across_restarts_of_the_same_instance(self, monkeypatch):
        """The property that bounds the queue count: a restarted process
        re-adopts its own queue instead of minting another."""
        _cf_env(monkeypatch, index="1")
        before = resolve_instance_id("")
        after = resolve_instance_id("")  # a "restart" — same index, same app
        assert before == after

    def test_distinct_across_replicas(self, monkeypatch):
        _cf_env(monkeypatch, index="0")
        zero = resolve_instance_id("")
        _cf_env(monkeypatch, index="1")
        one = resolve_instance_id("")
        assert zero != one

    def test_two_apps_at_the_same_index_do_not_collide(self, monkeypatch):
        """A bare index would make instance 0 of two apps sharing one broker
        namespace hash to the same response topic."""
        _cf_env(monkeypatch, index="0", app_name="ai-workflow-control-plane")
        cp = response_topic_for(resolve_instance_id(""))
        _cf_env(monkeypatch, index="0", app_name="wio-executor")
        executor = response_topic_for(resolve_instance_id(""))
        assert cp != executor

    @pytest.mark.parametrize(
        "vcap",
        [
            "",  # not on CF
            "not json at all",
            "[]",  # valid JSON, wrong shape
            "{}",  # object without a name
            '{"application_name": ""}',
            '{"application_name": 42}',
        ],
    )
    def test_unusable_vcap_yields_no_derivation(self, monkeypatch, vcap):
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        monkeypatch.setenv("VCAP_APPLICATION", vcap)
        assert cf_instance_identity() == ""

    def test_legacy_name_alias_is_accepted(self, monkeypatch):
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"name": "legacy-app"}))
        assert cf_instance_identity() == "legacy-app_0"

    def test_missing_index_yields_no_derivation(self, monkeypatch):
        monkeypatch.delenv("CF_INSTANCE_INDEX", raising=False)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": APP_NAME}))
        assert cf_instance_identity() == ""

    def test_index_zero_is_a_real_index_not_a_falsy_one(self, monkeypatch):
        """``"0"`` is the FIRST replica, not "absent" — a truthiness check here
        would push instance 0 onto the random fallback."""
        _cf_env(monkeypatch, index="0")
        assert cf_instance_identity() == f"{APP_NAME}_0"


class TestRandomFallback:
    def test_off_cloud_foundry_yields_a_usable_unique_id(self, monkeypatch):
        _no_cf(monkeypatch)
        first = resolve_instance_id("")
        second = resolve_instance_id("")
        assert first and second and first != second

    def test_fallback_never_raises_on_a_broken_vcap(self, monkeypatch):
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        monkeypatch.setenv("VCAP_APPLICATION", "{ this is not json")
        assert resolve_instance_id("")  # degrades, does not explode


class TestBootstrapUsesTheResolver:
    """Guard the wiring, not just the helper. The leak was never in the
    helper — it was that the call site took ``settings.INSTANCE_ID or uuid4()``
    and no manifest supplied the left-hand side for this app.
    """

    @staticmethod
    def _bootstrap_source() -> str:
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "bootstrap.py"
        assert path.is_file(), f"bootstrap.py not found at {path}"
        return path.read_text(encoding="utf-8")

    # Both assert on a bool, not on the membership expression: pytest would
    # otherwise render the whole of bootstrap.py into the failure output.

    def test_identity_comes_from_the_resolver(self):
        wired = "resolve_instance_id(settings.INSTANCE_ID)" in self._bootstrap_source()
        assert wired, (
            "bootstrap.py must resolve the instance id through "
            "resolve_instance_id() so the identity is stable on Cloud Foundry"
        )

    def test_no_bare_uuid_fallback_remains_at_the_call_site(self):
        leaky = "settings.INSTANCE_ID or str(uuid.uuid4())" in self._bootstrap_source()
        assert not leaky, (
            "a bare INSTANCE_ID-or-uuid4 call site mints a NEW response-topic "
            "queue on every boot; only a graceful shutdown ever reclaims it"
        )
