"""Bounded, stable process identity for per-instance broker resources.

Every process that runs ``ResponseTopicListener`` provisions a broker queue
named after its identity (``sub-orchestration-responses-<shard>``, where the
shard is ``sha256(instance_id)[:8]``). So the identity decides how many queues
a deployment consumes over its lifetime:

* a **random** id mints a fresh queue on every boot, and the old one is
  reclaimed only by a graceful ``stop()``. Any shutdown that skips the lifespan
  handler — SIGKILL past the grace period, OOM, crash, a ``delete_topic_queue``
  that 403s — leaves an orphan that nothing can ever re-adopt. After enough
  deploys the Event Mesh plan quota fills and ``create_queue`` returns 422, at
  which point new pods boot ``degraded`` and drop cross-instance responses.
* a **stable** id makes a restarted process re-adopt its own queue, so the
  count is bounded by the replica count instead of the deploy count.

SA-2087 established that and pinned ``INSTANCE_ID`` in the CF manifest. Two
things went wrong with resolving it there, and this module is why identity is
now resolved in-process instead:

1. The pin landed in ``deployment-executor.yml`` only. The control plane's own
   manifest (``deployment.yml``) never got an ``INSTANCE_ID``, so that app kept
   falling through to the random default — the leak this module removes.
2. The pinned value is ``<app>_${CF_INSTANCE_INDEX}``, but nothing expands it.
   Cloud Foundry does not interpolate ``${...}`` in manifest env values (its own
   manifest variables use ``((var))``), the deploy pipeline writes the value
   verbatim, and the app reads it with a plain ``os.getenv``. The process
   therefore sees the literal string — identical on every replica, which is the
   guaranteed shard collision SA-2087 set out to remove.

Cloud Foundry already injects everything needed to build the same identity from
inside the container: ``CF_INSTANCE_INDEX`` (Diego keys an ActualLRP by
``{ProcessGuid, Index, Domain}``, and a crash clears only the
``ActualLRPInstanceKey``, so the index survives restarts) and
``VCAP_APPLICATION.application_name``. Deriving from those makes the manifest
non-load-bearing: correct on both apps, on any tier, with no deploy-time
substitution to get wrong.
"""
from __future__ import annotations

import json
import logging
import os
import uuid

_logger = logging.getLogger(__name__)


def _is_unexpanded_placeholder(value: str) -> bool:
    """True when *value* still carries a shell-style ``${...}`` placeholder.

    A configured id that reaches the process as ``wio-executor_${CF_INSTANCE_INDEX}``
    is not an identity — it is the *same* literal on every replica, so honouring
    it would collapse the whole deployment onto one response topic. Treat it as
    absent and let the CF derivation below produce the value the manifest author
    meant.
    """
    return "${" in value


def _cf_application_name() -> str:
    """``VCAP_APPLICATION.application_name`` — empty when not on CF.

    Never raises: a malformed or non-object ``VCAP_APPLICATION`` degrades to
    "no name", which drops the caller to the random fallback rather than
    breaking startup.
    """
    raw = os.getenv("VCAP_APPLICATION", "").strip()
    if not raw:
        return ""
    try:
        vcap = json.loads(raw)
    except ValueError:
        _logger.warning("VCAP_APPLICATION is not valid JSON — cannot derive an instance id")
        return ""
    if not isinstance(vcap, dict):
        return ""
    # ``application_name`` is the documented field; ``name`` is the older alias
    # some CF versions still emit.
    for key in ("application_name", "name"):
        value = vcap.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def cf_instance_identity() -> str:
    """``<application_name>_<CF_INSTANCE_INDEX>`` from the container's own env.

    Empty when either half is missing — i.e. anywhere that is not a CF
    container (local runs, unit tests, docker-compose).

    The app-name prefix is required, not decoration: a bare index would make
    instance 0 of two apps sharing one broker namespace hash to the same
    response topic, reintroducing the collision across apps instead of across
    replicas. The separator is ``_`` to match the shape SA-2087 chose for the
    manifest pin, so an already-correct explicit ``INSTANCE_ID`` and a derived
    one produce the same topic.
    """
    index = os.getenv("CF_INSTANCE_INDEX", "").strip()
    if not index:
        return ""
    app_name = _cf_application_name()
    if not app_name:
        return ""
    return f"{app_name}_{index}"


def resolve_instance_id(configured: str = "") -> str:
    """Resolve this process's identity, preferring stability over novelty.

    Order:

    1. ``configured`` (the ``INSTANCE_ID`` env var) when it is a real value —
       test harnesses that run two bootstraps against one broker stub rely on
       being able to force it.
    2. The CF-derived ``<app>_<index>``, which is stable across restarts and
       distinct per replica.
    3. A random UUID. Correct for routing (uniqueness is all routing needs, and
       the futures it routes to die with the process) but **unbounded** in
       broker queues, so it is the last resort and says so in the log.
    """
    configured = (configured or "").strip()
    if configured and not _is_unexpanded_placeholder(configured):
        return configured
    if configured:
        _logger.warning(
            "INSTANCE_ID=%r still contains an unexpanded '${...}' placeholder — "
            "Cloud Foundry does not interpolate manifest env values, so this is "
            "the same literal on every replica. Deriving the identity from the "
            "CF container env instead.",
            configured,
        )
    derived = cf_instance_identity()
    if derived:
        _logger.info("Instance identity derived from the CF container env: %s", derived)
        return derived
    fallback = str(uuid.uuid4())
    _logger.info(
        "No stable instance identity available (INSTANCE_ID unset and not "
        "running on Cloud Foundry) — falling back to a random id %s. Routing is "
        "correct, but each boot provisions a NEW per-instance broker queue that "
        "only a graceful shutdown reclaims.",
        fallback,
    )
    return fallback
