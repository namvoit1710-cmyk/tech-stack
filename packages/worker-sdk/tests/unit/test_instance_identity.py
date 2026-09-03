"""R8 (SA-2055) §2 — the SDK's answer to "which process am I?".

instance_id = f"{application_name}_{CF_INSTANCE_INDEX}", both halves from CF.
INDEX, never GUID: Diego keys an ActualLRP by ActualLRPKey{ProcessGuid, Index,
Domain} and clears/reissues only the ActualLRPInstanceKey on a crash, so the
INDEX survives a crash-restart and the GUID does not (keystone §6.1). A stable
id makes a restarted replica RE-USE its registry rows instead of orphaning one
per restart, unbounded.

Off CF -> blank -> the executor uuid4s per register call, which is already
correct for counting (keystone §4.2). Fails safe, never worse than today.
"""

import json
import os
import re
import socket

from worker_sdk.layer4_frameworks.config import instance_identity


def _clear_cf(monkeypatch):
    for var in ("VCAP_APPLICATION", "CF_INSTANCE_INDEX", "CF_INSTANCE_IP"):
        monkeypatch.delenv(var, raising=False)


class TestInstanceId:
    def test_composes_app_name_and_index(self, monkeypatch):
        """The spec's headline case (§9 checkbox 1)."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        assert instance_identity.instance_id() == "jira-worker_0"

    def test_non_zero_index(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "2")
        assert instance_identity.instance_id() == "jira-worker_2"

    def test_stable_across_calls_with_the_same_env(self, monkeypatch):
        """The whole point: same INDEX -> same id -> the restarted replica
        overwrites its own rows (keystone §5, the 'restart' row)."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "w"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "1")
        assert instance_identity.instance_id() == instance_identity.instance_id() == "w_1"


class TestBlankFallback:
    def test_both_absent_is_blank(self, monkeypatch):
        """§9 checkbox 2: off CF -> blank -> executor path unchanged from today."""
        _clear_cf(monkeypatch)
        assert instance_identity.instance_id() == ""

    def test_index_without_vcap_is_blank(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        assert instance_identity.instance_id() == ""

    def test_vcap_without_index_is_blank(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "w"}))
        assert instance_identity.instance_id() == ""

    def test_malformed_vcap_is_blank_not_a_crash(self, monkeypatch):
        """A bad VCAP must degrade to the uuid4 path, never take registration down."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        for bad in ("{not json", "null", '"a string"', "[1,2]", "{}", '{"application_name": ""}'):
            monkeypatch.setenv("VCAP_APPLICATION", bad)
            assert instance_identity.instance_id() == "", bad


class TestAppNameIsNeverIdentity:
    """§9 checkbox 3 — APP_NAME is a DISPLAY name, not the CF app name."""

    def test_app_name_is_not_a_name_fallback_when_vcap_is_absent(self, monkeypatch):
        """The exact trap, with teeth: CF_INSTANCE_INDEX is present (so the index
        guard passes and execution REACHES the name half), APP_NAME is set, but
        VCAP_APPLICATION is absent. Correct code has no name -> blank. Code that
        wrongly fell back to APP_NAME for the name would return
        "HTTP Request Worker_0" — this asserts it does not.
        (The earlier version set no index, so it returned blank at the index guard
        before APP_NAME was ever consulted — green whether or not the bug existed.)"""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        monkeypatch.setenv("APP_NAME", "HTTP Request Worker")
        assert instance_identity.instance_id() == ""

    def test_app_name_never_leaks_into_the_id(self, monkeypatch):
        """On CF with BOTH set, the id comes from VCAP only — APP_NAME is a decoy."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("APP_NAME", "HTTP Request Worker")
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "http-request-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        got = instance_identity.instance_id()
        assert got == "http-request-worker_0"
        assert "HTTP Request Worker" not in got
        assert " " not in got          # a display name has spaces; an id must not

    def test_settings_app_name_is_not_consulted(self, monkeypatch):
        """Even mutating the loaded Settings object cannot move the identity —
        proof the module reads env/VCAP and nothing else."""
        _clear_cf(monkeypatch)
        from worker_sdk.layer4_frameworks.config.app_config import settings
        monkeypatch.setattr(settings, "APP_NAME", "Totally Different Worker")
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "3")
        assert instance_identity.instance_id() == "jira-worker_3"


class TestHostPidStartedAt:
    def test_host_prefers_cf_instance_ip(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("CF_INSTANCE_IP", "10.0.1.23")
        assert instance_identity.host() == "10.0.1.23"

    def test_host_falls_back_to_hostname(self, monkeypatch):
        _clear_cf(monkeypatch)
        assert instance_identity.host() == socket.gethostname()
        assert instance_identity.host() != ""

    def test_pid_is_this_process(self):
        assert instance_identity.pid() == os.getpid()
        assert instance_identity.pid() > 0

    def test_started_at_is_iso8601_utc(self):
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", instance_identity.STARTED_AT)
        assert instance_identity.STARTED_AT.endswith("+00:00")

    def test_started_at_is_frozen_at_import(self):
        """spec §5: 22 register calls from one process must report ONE start time.
        A module CONSTANT delivers that; a function would not. Reading it twice,
        with real time passing in between, must give the identical string."""
        import time
        first = instance_identity.STARTED_AT
        time.sleep(0.01)
        assert instance_identity.STARTED_AT == first

    def test_started_at_survives_reimport(self):
        """Re-importing the module must not re-stamp it (import caching is the
        mechanism the freeze relies on)."""
        first = instance_identity.STARTED_AT
        import importlib
        again = importlib.import_module("worker_sdk.layer4_frameworks.config.instance_identity")
        assert again.STARTED_AT == first


class TestWorkerApp:
    """R14 — the deployable's name, so the executor no longer has to derive an
    ugly-but-honest value (``smdg-ai-tenant-1-worker-jira-worker``) from the
    endpoint host. Unlike instance_id(), this reads ONLY VCAP_APPLICATION —
    no CF_INSTANCE_INDEX guard — because the app name is identical across
    every replica, which is precisely why it is useful as a grouping key."""

    def test_worker_app_reads_vcap_application_name(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        assert instance_identity.worker_app() == "jira-worker"

    def test_worker_app_does_not_require_cf_instance_index(self, monkeypatch):
        """The discriminating half of the design: instance_id() needs BOTH
        VCAP_APPLICATION and CF_INSTANCE_INDEX. worker_app() must resolve from
        VCAP_APPLICATION alone — if the implementation wrongly copy-pasted the
        instance_id() index guard, this would return "" instead of the name."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        assert "CF_INSTANCE_INDEX" not in os.environ
        assert instance_identity.worker_app() == "jira-worker"

    def test_worker_app_blank_without_vcap(self, monkeypatch):
        _clear_cf(monkeypatch)
        assert instance_identity.worker_app() == ""

    def test_worker_app_blank_on_malformed_vcap(self, monkeypatch):
        _clear_cf(monkeypatch)
        for bad in ("not-json", "{not json", "null", '"a string"', "[1,2]", "{}",
                    '{"application_name": ""}'):
            monkeypatch.setenv("VCAP_APPLICATION", bad)
            assert instance_identity.worker_app() == "", bad

    def test_worker_app_never_reads_app_name_env_var(self, monkeypatch):
        """§ the exact trap from instance_id(): APP_NAME is a human DISPLAY
        name ("HTTP Request Worker") and is not unique by construction. If
        worker_app() ever fell back to it, this would return the spaced
        display string instead of "" (VCAP is absent here)."""
        _clear_cf(monkeypatch)
        monkeypatch.setenv("APP_NAME", "HTTP Request Worker")
        assert instance_identity.worker_app() == ""

    def test_worker_app_stable_across_calls(self, monkeypatch):
        _clear_cf(monkeypatch)
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": "jira-worker"}))
        assert instance_identity.worker_app() == instance_identity.worker_app() == "jira-worker"
