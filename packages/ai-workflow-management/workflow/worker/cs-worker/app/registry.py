"""The project x environment registry — built from the ENVIRONMENT at startup.

This is the one thing the cs-worker has that the log-worker does not. In the log-worker
the set of targets is a **literal in the code** (``_SUFFIX = {"mck_dev": …, "alcon_qas":
…}``) and the endpoint URLs live on the workflow nodes, so onboarding a project means a
code change *and* a new workflow per project+env. Here a target is whatever the process
environment says it is:

    LOG_WORKER_CLIENT_ID_MCK_DEV=<the CommonProcessService OAuth client for MCK dev>
    LOG_WORKER_CLIENT_SECRET_MCK_DEV=…
    LOG_WORKER_APP_PREFIX_MCK_DEV=mckesson-dev-simplemdg
    LOG_WORKER_REGION_MCK_DEV=us20

Four variables, then restart the container: `MCK` / `DEV` is live, and every node and
every workflow that asks for it works unchanged. Nothing else is edited.

Naming
------
``LOG_WORKER_CLIENT_ID_<PROJECT>_<ENV>`` — the trailing token is the ENVIRONMENT, the
rest is the PROJECT. Split on the LAST underscore, which is what makes ``S4_QAS`` read
as project ``S4`` / env ``QAS`` rather than project ``S4_QAS``. A name with no
underscore left after the prefix (the deprecated ``LOG_WORKER_CLIENT_ID_QAS``) names no
project and is ignored — deliberately, because guessing a project for it is how one
customer's credentials end up pointed at another's host.

The ``LOG_WORKER_`` prefix is kept (rather than a new ``CS_WORKER_``) so the operator's
existing ``log-worker/.env`` can be copied across as-is.

Endpoints
---------
Derived from ``APP_PREFIX`` + ``REGION`` because every landscape measured follows the
same three shapes:

    token : https://<prefix>.authentication.<auth_region>.hana.ondemand.com/oauth/token
    api   : https://<prefix>-srv-process-system.cfapps.<region>.hana.ondemand.com/CommonProcessService
    abap  : https://<prefix>-srv-config-admin.cfapps.<region>.hana.ondemand.com/AdminService/<entity>
    web   : https://<prefix>-web.cfapps.<web_region>.hana.ondemand.com  (for the CR deep link)

``AUTH_REGION`` and ``WEB_REGION`` both default to ``REGION``; set them only for a
landscape that splits, as ALCON QAS does (apps in us10-003, UAA in us10).

Any of them can be overridden per target (``LOG_WORKER_API_URL_<T>`` etc.) for a
landscape that does not fit.

Cloud Logging
-------------
``LOG_WORKER_CLS_URL_<T>`` / ``…_CLS_USERNAME_<T>`` / ``…_CLS_PASSWORD_<T>``, per target
like everything else. It used to be a single global ``CLS_URL`` — "one instance for the
whole landscape" — which is true until a second customer has its own. All three are
optional: a landscape with no Cloud Logging still serves process logs, and
``Target.cls_ready`` is what distinguishes "not configured" from "broken". A PARTIAL set
counts as not ready, because two values out of three authenticate as nobody and the 401
that follows is much harder to read than the missing variable's name.

``LOG_WORKER_ABAP_ENTITY_<T>`` exists because the ABAP log is **not universal**. Measured
2026-08-24 against all five configured landscapes, by asking each AdminService for its own
entity list:

| target | AdminService serves |
|---|---|
| MCK DEV | ``MDApiLog`` — real rows |
| S4 QAS | ``MDApiLog`` — real rows (``CeAppLogMsg``, which the published S4 flow uses, answers 502) |
| MCK QAS | neither — the service exposes CompanySetting/GlobalSettings/… |
| ALCON DEV, ALCON QAS | neither — a different AdminService entirely (Questionnaires/Questions/…) |

So ``LOG_WORKER_ABAP_ENTITY_<T>=none`` marks a landscape that has no ABAP log. The node
then says so in one sentence instead of returning a raw OData 404 that a reader has to
decode — which is what the published per-project flows do today, silently, because a
failed fetch still reports the task as SUCCESS.

An incomplete target is NOT silently dropped. It is kept with a ``problem`` string, so
``cs_targets`` can answer "why is MCK PRD not showing up?" with "no
LOG_WORKER_CLIENT_SECRET_MCK_PRD" instead of silence. Only complete targets are
resolvable.

Nothing here logs, returns or formats a secret VALUE — only whether one is present.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

_PREFIX = "LOG_WORKER_"
_ID_RE = re.compile(r"^LOG_WORKER_CLIENT_ID_(?P<target>[A-Z0-9_]+)$")

# Per-target knobs, as env-var infixes. All optional except the credential pair.
_KEYS = ("CLIENT_ID", "CLIENT_SECRET", "APP_PREFIX", "REGION",
         "API_URL", "TOKEN_URL", "ABAP_URL", "ABAP_ENTITY",
         # Cloud Logging (OpenSearch). Optional: a landscape may have none, and the
         # process-log fetch must keep working when it does not.
         "CLS_URL", "CLS_USERNAME", "CLS_PASSWORD")

_DEFAULT_ABAP_ENTITY = "MDApiLog"

# The path the SMDG web app uses to show one change request. Appended to a target's web
# base to make the link a reply can hand the user.
_CR_PATH = "/main/index.html#/requestHistory/"

# The path the SMDG web app uses to show one change request. Appended to the target's
# web base to make the link a reply can hand the user.
_CR_PATH = "/main/index.html#/requestHistory/"


# Environment spellings a human uses for the same landscape. Applied ONLY as a fallback
# and ONLY when the normalised name actually exists for that project — so an alias can
# never invent a target, it can only find one that is already configured.
_ENV_ALIASES: dict[str, str] = {
    "DEV": "DEV", "DEVELOP": "DEV", "DEVELOPMENT": "DEV", "D": "DEV",
    "QAS": "QAS", "QA": "QAS", "QUALITY": "QAS", "TEST": "QAS", "UAT": "QAS",
    "PRD": "PRD", "PROD": "PRD", "PRODUCTION": "PRD", "P": "PRD",
}


@dataclass(frozen=True)
class Target:
    """One resolvable project + environment: who to log in as, and where to GET."""

    project: str
    environment: str
    client_id: str
    client_secret: str
    token_url: str
    api_url: str          # base up to /CommonProcessService
    # base up to /RequestorService, on the srv-process-requestor app. This is where
    # cancelRequest lives -- the action the UI's Cancel button calls, and the only cancel
    # that exists on every landscape checked (2026-09-07). Empty when the landscape has
    # no such app.
    requestor_url: str = ""
    abap_url: str = ""
    web_url: str = ""     # SMDG web app base; '' when the landscape has none
    # Cloud Logging is per-landscape, not one instance for all of them. Empty is a valid
    # state - see `cls_ready` - and never blocks the process-log fetch.
    cls_url: str = ""
    cls_username: str = ""
    cls_password: str = ""
    problem: str = ""     # non-empty => configured but NOT usable, and why

    @property
    def key(self) -> str:
        return f"{self.project}_{self.environment}"

    @property
    def usable(self) -> bool:
        return not self.problem

    @property
    def cls_ready(self) -> bool:
        """All three Cloud Logging values present. A partial set is NOT ready: two out of
        three authenticates as nobody, and the resulting 401 is far harder to read than a
        sentence naming the variable that is missing."""
        return bool(self.cls_url and self.cls_username and self.cls_password)

    def cls_problem(self) -> str:
        """Why Cloud Logging cannot be searched for this target, or ''."""
        if self.cls_ready:
            return ""
        missing = [f"${_PREFIX}CLS_{k}_{self.key}"
                   for k, v in (("URL", self.cls_url), ("USERNAME", self.cls_username),
                                ("PASSWORD", self.cls_password)) if not v]
        return "Cloud Logging is not configured for " + self.key + ": set " + ", ".join(missing)

    def url_for(self, service: str) -> str:
        """The endpoint this service GETs, derived from the target's own bases."""
        base = self.api_url.rstrip("/")
        if service == "request_action_log":
            return base + "/RequestActionLog"
        if service == "submit_event_log":
            return base + "/SubmitEventLog"
        if service == "abap_log":
            return self.abap_url
        return base  # request_log — navigation style, base only

    def cr_url(self, req_id: str) -> str:
        """Deep link to one change request in that landscape's web app, or '' when the
        landscape has none.

        Returning '' rather than a guessed URL is deliberate: a dead link in a reply to a
        customer costs more trust than no link at all."""
        req_id = (req_id or "").strip()
        if not (self.web_url and req_id):
            return ""
        return self.web_url.rstrip("/") + _CR_PATH + req_id

    def describe(self) -> dict[str, Any]:
        """Safe to log / return to a workflow: endpoints yes, credentials never."""
        return {"project": self.project, "environment": self.environment,
                "token_url": self.token_url, "api_url": self.api_url,
                "requestor_url": self.requestor_url,
                "abap_url": self.abap_url, "web_url": self.web_url, "usable": self.usable,
                # Endpoint yes, credentials never - only whether they are present.
                "cls_url": self.cls_url, "cls_ready": self.cls_ready,
                "problem": self.problem}


def _env(target: str, key: str) -> str:
    return (os.environ.get(f"{_PREFIX}{key}_{target}") or "").strip()


def _split(target: str) -> Optional[tuple[str, str]]:
    """``MCK_DEV`` -> ``("MCK", "DEV")``; ``S4_QAS`` -> ``("S4", "QAS")``.

    Split on the LAST underscore. ``None`` when there is nothing before it — the
    deprecated un-projected names (``…_CLIENT_ID_QAS``) land here and are skipped."""
    project, sep, environment = target.rpartition("_")
    if not sep or not project or not environment:
        return None
    return project, environment


def _build_target(target: str) -> Target:
    parts = _split(target)
    if parts is None:
        raise ValueError(target)
    project, environment = parts

    client_id = _env(target, "CLIENT_ID")
    client_secret = _env(target, "CLIENT_SECRET")
    prefix = _env(target, "APP_PREFIX")
    region = _env(target, "REGION")
    # A landscape does not have to keep its apps, its UAA and its web app in one region.
    # ALCON QAS (measured 2026-08-25) runs apps in us10-003 and UAA in us10, while its
    # API region formula-derived from br10 pointed at routes that do not exist — that
    # target had never worked. Both default to REGION, so the common case stays 4 vars.
    auth_region = _env(target, "AUTH_REGION") or region
    web_region = _env(target, "WEB_REGION") or region
    # "none" is a real answer: this landscape has no ABAP MD API log (see the table in
    # the module docstring). It blanks abap_url, and fetch_log turns that into a sentence.
    entity = _env(target, "ABAP_ENTITY") or _DEFAULT_ABAP_ENTITY
    abap_available = entity.strip().lower() not in ("none", "-", "off", "no")

    token_url = _env(target, "TOKEN_URL")
    api_url = _env(target, "API_URL")
    requestor_url = _env(target, "REQUESTOR_URL")
    abap_url = _env(target, "ABAP_URL")
    # "none" = this landscape has no web app, so replies carry no CR link for it.
    web_url = _env(target, "WEB_URL")
    web_available = web_url.strip().lower() not in ("none", "-", "off", "no")
    # "none" = this landscape has no web app, so replies carry no CR link for it.
    web_url = _env(target, "WEB_URL")
    web_available = web_url.strip().lower() not in ("none", "-", "off", "no")

    if prefix and region:
        token_url = token_url or (
            f"https://{prefix}.authentication.{auth_region}.hana.ondemand.com/oauth/token")
        api_url = api_url or (
            f"https://{prefix}-srv-process-system.cfapps.{region}"
            f".hana.ondemand.com/CommonProcessService")
        # Same prefix+region formula, a different app. Probing for it by guessing names
        # cost an afternoon; the name comes from the CDS module, srv_process_requestor.
        requestor_url = requestor_url or (
            f"https://{prefix}-srv-process-requestor.cfapps.{region}"
            f".hana.ondemand.com/RequestorService")
        if abap_available:
            abap_url = abap_url or (
                f"https://{prefix}-srv-config-admin.cfapps.{region}"
                f".hana.ondemand.com/AdminService/{entity}")
        if web_available:
            web_url = web_url or (
                f"https://{prefix}-web.cfapps.{web_region}.hana.ondemand.com")
        if web_available:
            web_url = web_url or (
                f"https://{prefix}-web.cfapps.{web_region}.hana.ondemand.com")

    # One precise reason, in the order the operator would fix them.
    missing: list[str] = []
    if not client_secret:
        missing.append(f"${_PREFIX}CLIENT_SECRET_{target}")
    if not (token_url and api_url):
        missing.append(f"${_PREFIX}APP_PREFIX_{target} + ${_PREFIX}REGION_{target}"
                       f" (or ${_PREFIX}API_URL_{target} + ${_PREFIX}TOKEN_URL_{target})")
    problem = ("not configured: " + ", ".join(missing)) if missing else ""

    if not abap_available:
        abap_url = ""
    if not web_available:
        web_url = ""
    if not web_available:
        web_url = ""

    return Target(project=project, environment=environment,
                  client_id=client_id, client_secret=client_secret,
                  token_url=token_url, api_url=api_url,
                  requestor_url=requestor_url, abap_url=abap_url,
                  web_url=web_url,
                  cls_url=_env(target, "CLS_URL"),
                  cls_username=_env(target, "CLS_USERNAME"),
                  cls_password=_env(target, "CLS_PASSWORD"),
                  problem=problem)


@dataclass
class Registry:
    """Every target the process environment declares, keyed ``PROJECT_ENV``."""

    targets: dict[str, Target] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)   # ALIAS -> PROJECT

    # -- lookups --------------------------------------------------------------

    def projects(self) -> list[str]:
        """Projects with at least one usable environment, alphabetical."""
        return sorted({t.project for t in self.targets.values() if t.usable})

    def environments(self, project: str) -> list[str]:
        p = self.normalise_project(project)
        return sorted(t.environment for t in self.targets.values()
                      if t.usable and t.project == p)

    def normalise_project(self, project: str) -> str:
        """Whatever the user typed -> the configured project name, or ''."""
        raw = (project or "").strip().upper().replace("-", "_").replace(" ", "_")
        if not raw:
            return ""
        if any(t.project == raw for t in self.targets.values()):
            return raw
        return self.aliases.get(raw, "")

    def normalise_environment(self, project: str, environment: str) -> str:
        """Whatever the user typed -> an environment configured for *project*, or ''."""
        p = self.normalise_project(project)
        if not p:
            return ""
        raw = (environment or "").strip().upper().replace("-", "_").replace(" ", "_")
        if not raw:
            return ""
        known = self.environments(p)
        if raw in known:
            return raw
        alias = _ENV_ALIASES.get(raw, "")
        return alias if alias in known else ""

    def resolve(self, project: str, environment: str) -> Optional[Target]:
        """The usable Target for this pair, or ``None``. Never falls back to another
        project or environment — a silent fallback here means logging in to customer A
        with customer B's expectations."""
        p = self.normalise_project(project)
        e = self.normalise_environment(p, environment)
        if not (p and e):
            return None
        t = self.targets.get(f"{p}_{e}")
        return t if (t and t.usable) else None

    # -- presentation ---------------------------------------------------------

    def catalogue(self) -> str:
        """One line the model and the end user both read: ``MCK (DEV, QAS); S4 (QAS)``."""
        return "; ".join(f"{p} ({', '.join(self.environments(p))})" for p in self.projects())

    def describe(self) -> list[dict[str, Any]]:
        return [self.targets[k].describe() for k in sorted(self.targets)]

    def problems(self) -> list[str]:
        return [f"{k}: {self.targets[k].problem}"
                for k in sorted(self.targets) if self.targets[k].problem]


def _load_aliases() -> dict[str, str]:
    """``LOG_WORKER_PROJECT_ALIASES_MCK=McKesson,MCKESSON`` -> ``{MCKESSON: MCK}``.

    A deterministic spelling map, so "the McKesson DEV logs" resolves without depending
    on the model getting the token right."""
    out: dict[str, str] = {}
    for name, value in os.environ.items():
        if not name.startswith(f"{_PREFIX}PROJECT_ALIASES_"):
            continue
        project = name[len(f"{_PREFIX}PROJECT_ALIASES_"):].strip().upper()
        for alias in (value or "").split(","):
            alias = alias.strip().upper().replace("-", "_").replace(" ", "_")
            if alias and project:
                out[alias] = project
    return out


def load_registry() -> Registry:
    """Read the environment once. Called at import and by ``reload()``."""
    targets: dict[str, Target] = {}
    for name in os.environ:
        m = _ID_RE.match(name)
        if not m:
            continue
        raw = m.group("target")
        if _split(raw) is None:
            continue          # deprecated un-projected alias — see module docstring
        try:
            t = _build_target(raw)
        except ValueError:
            continue
        targets[t.key] = t
    return Registry(targets=targets, aliases=_load_aliases())


# Built once at import. The environment of a running container does not change, so a
# rebuild per task would buy nothing; picking up a new project is a restart, which is
# the contract the operator asked for.
REGISTRY: Registry = load_registry()


def reload() -> Registry:
    """Re-read the environment (tests, and a SERVER-mode process that was re-exec'd)."""
    global REGISTRY
    REGISTRY = load_registry()
    return REGISTRY
