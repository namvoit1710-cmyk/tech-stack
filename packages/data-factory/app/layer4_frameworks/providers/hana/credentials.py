"""Where a HANA password comes from when the payload does not carry one.

Two lookups, both pure infrastructure: what the platform bound to this app, and
what the operator configured on it. Neither knows anything about migrations or
validation, which is why they live here rather than beside either caller.

The *ordering* between them - and the dispatch-password and resolve-token steps
either side - is policy and stays with the feature that owns it. This module only
answers "is there one here, for this host and this user".

Both are keyed on host AND user, so one DF can serve several sources without ever
handing the wrong secret to the wrong database. That matters more than it looks:
the payload names which HANA and which schema, and DF holds the credential, so the
match is the only thing keeping those two facts in agreement.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def vcap_password(host: str, user: str) -> Optional[str]:
    """A bound HANA service instance, if CloudFoundry gave us one.

    This is the production answer: the platform hands DF the credential, so nothing
    has to carry it in a payload and nothing has to be asked for it at runtime.

    A binding with no password, or one for a different host or user, is skipped
    rather than returned - a near-miss here would authenticate against the wrong
    database with the right-looking secret.
    """
    raw = os.environ.get("VCAP_SERVICES")
    if not raw:
        return None
    try:
        services = json.loads(raw)
    except ValueError:
        logger.warning("VCAP_SERVICES is not valid JSON; ignoring it")
        return None
    for instances in services.values():
        for instance in instances or []:
            creds = (instance or {}).get("credentials") or {}
            if not creds.get("password"):
                continue
            if creds.get("host") and str(creds["host"]).lower() != str(host).lower():
                continue
            if creds.get("user") and str(creds["user"]) != user:
                continue
            return str(creds["password"])
    return None


def configured_password(host: str, user: str) -> Optional[str]:
    """DF's own configuration.

    DF_HANA_CREDENTIALS is a JSON object keyed by "user@host", so one DF can serve
    several sources. DF_HANA_PASSWORD is the single-source shorthand.
    """
    blob = os.environ.get("DF_HANA_CREDENTIALS")
    if blob:
        try:
            table = json.loads(blob)
        except ValueError:
            logger.warning("DF_HANA_CREDENTIALS is not valid JSON; ignoring it")
            table = {}
        for key in (f"{user}@{host}", host, user):
            if table.get(key):
                return str(table[key])
    return os.environ.get("DF_HANA_PASSWORD") or None


def resolve(host: str, user: str, password: Optional[str] = None) -> tuple[str, str]:
    """The secret and where it came from, for a caller with no token exchange.

    Same order as the migration's first three steps. The source is returned rather
    than logged so a caller can report it: a credential that silently came from
    somewhere unexpected is the hardest kind of misconfiguration to see.
    """
    if password:
        return password, "payload"
    bound = vcap_password(host, user)
    if bound:
        return bound, "service-binding"
    configured = configured_password(host, user)
    if configured:
        return configured, "df-config"
    raise PermissionError(
        f"no HANA password available for {user}@{host}. Supply it on the request, "
        "bind a HANA service instance to this app, or set DF_HANA_CREDENTIALS."
    )
