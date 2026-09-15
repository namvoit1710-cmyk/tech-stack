"""SA-2047 R14 — the deployable that owns a registration.

A worker APP is the third identity in this system, distinct from the other two:
``worker_type`` is a node type (a class of work), ``instance_id`` is a process,
and ``worker_app`` is the deployable that process runs as. jira-worker registers
22 worker_types from 1 process of 1 app; only this key can answer "how many
workers do we have".

The SDK sends the CF application_name. When it does not (an image predating
SA-2055/R14), the executor derives the app from the endpoint host at the
boundary. The derivation returns the first DNS label VERBATIM -- no prefix
stripping. Trimming ``smdg-ai-<tenant>-worker-`` would encode a naming
convention the registry does not own, and would silently collide the day a
deployment names things differently. Ugly-but-exact beats pretty-but-guessed;
the SDK value replaces it once the images rebuild.
"""
from __future__ import annotations


def worker_app_from_endpoint(endpoint_url: str) -> str:
    """First DNS label of the endpoint host, or "" when undeterminable.

    "" is a real answer, not a failure: it groups under one honestly-unnamed
    bucket rather than inventing a name.
    """
    raw = (endpoint_url or "").strip()
    if not raw:
        return ""
    without_scheme = raw.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0].split("@")[-1]
    host = host.split(":", 1)[0]
    label = host.split(".", 1)[0].strip()
    return label
