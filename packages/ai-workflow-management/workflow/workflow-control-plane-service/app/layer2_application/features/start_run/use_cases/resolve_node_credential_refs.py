"""Moved — see ``app.layer2_application.credentials.node_credential_refs``.

The producer step gained a second caller (``ChildWorkflowCoordinator``, for a
sub-workflow's child run) and moved to the shared ``credentials`` package. A
shared collaborator importing back into ``features/start_run`` closes an import
cycle through this package's ``__init__``.

This module stays as a re-export so existing imports — including
``tests/unit/test_resolve_node_credential_refs.py`` — keep resolving.
"""

from app.layer2_application.credentials.node_credential_refs import (  # noqa: F401
    _node_credential_ref,
    _REF_KEY,
    _Resolver,
    collect_and_resolve,
    collect_refs,
)

__all__ = ["collect_and_resolve", "collect_refs"]
