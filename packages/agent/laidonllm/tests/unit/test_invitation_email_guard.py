"""Tests for litellm/proxy/management_helpers/invitation_email_guard.py (SA-2296).

Lives outside the litellm package for the same collection-import reason as
test_hana_config.py: litellm/proxy/ has an __init__.py, so a test placed
there is collected as `litellm.proxy.test_*` and imports litellm/__init__.py,
which does an unguarded `import dotenv` and dies in this lightweight env.
The module under test has no such import (stdlib-only), so it loads by file
path with no stubbing needed.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

_TARGET = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "litellm",
    "proxy",
    "management_helpers",
    "invitation_email_guard.py",
)
_SPEC = importlib.util.spec_from_file_location("invitation_email_guard", os.path.normpath(_TARGET))
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
is_email_missing_for_invitation = _MOD.is_email_missing_for_invitation


@pytest.mark.parametrize("user_email", [None, "", "   "])
def test_missing_or_blank_email_is_refused(user_email):
    assert is_email_missing_for_invitation(user_email) is True


@pytest.mark.parametrize("user_email", ["a@example.com", "  a@example.com  "])
def test_present_email_is_allowed(user_email):
    assert is_email_missing_for_invitation(user_email) is False
