"""Pure predicate for the invitation-requires-email rule (SA-2296).

Stdlib-only imports on purpose so it can be loaded and unit-tested without
importing the full litellm package - see litellm/proxy/db/hana/config.py for
the same pattern.
"""

from __future__ import annotations

from typing import Optional


def is_email_missing_for_invitation(user_email: Optional[str]) -> bool:
    """True when an invitation must be refused because the user has no email.

    Login via an invite link looks the user up by email (see
    `authenticate_user` in litellm/proxy/auth/login_utils.py), so creating an
    invitation for a user with no email lets them set a password they can
    never use to log in.
    """
    return user_email is None or not user_email.strip()
