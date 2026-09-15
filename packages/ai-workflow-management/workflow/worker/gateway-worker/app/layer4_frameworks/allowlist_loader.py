"""Load the external host allowlist from a YAML file.

Schema (see config/external.yaml for an annotated template):
  allowed_hosts: ["api.partner.com", "*.crm.example.com"]
  allowed_schemes: ["https"]    # optional, default ["https"]
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.layer1_domain.value_objects.external_url_policy import ExternalUrlPolicy


def load_external_url_policy(path: str | Path) -> ExternalUrlPolicy:
    path = Path(path)
    if not path.exists():
        return ExternalUrlPolicy(allowed_hosts=(), allowed_schemes=("https",))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    allowed_hosts = tuple(h.strip() for h in (raw.get("allowed_hosts") or []) if h and h.strip())
    allowed_schemes_raw = raw.get("allowed_schemes") or ["https"]
    allowed_schemes = tuple(s.strip().lower() for s in allowed_schemes_raw if s and s.strip())
    return ExternalUrlPolicy(
        allowed_hosts=allowed_hosts,
        allowed_schemes=allowed_schemes or ("https",),
    )
