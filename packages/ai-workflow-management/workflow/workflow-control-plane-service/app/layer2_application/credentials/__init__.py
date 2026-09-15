"""Run credentials: sealed at start, resolved at egress.

Two collaborators, deliberately split by WHEN they run:

* :class:`RunCredentialSeeder` — start time. Seals author-supplied
  ``runtime_credentials`` onto the run. It no longer talks to the vault.
* :class:`RunCredentialProvider` — dispatch time. Resolves the node's own
  ``credential_ref`` against the vault (SA-2287 / B3).
"""

from app.layer2_application.credentials.node_credential_refs import (
    collect_refs,
    credential_ref_of,
)
from app.layer2_application.credentials.run_credential_provider import (
    RunCredentialProvider,
)
from app.layer2_application.credentials.run_credential_seeder import (
    CREDENTIALS_KEY,
    RunCredentialSeeder,
)

__all__ = [
    "CREDENTIALS_KEY",
    "RunCredentialProvider",
    "RunCredentialSeeder",
    "collect_refs",
    "credential_ref_of",
]
