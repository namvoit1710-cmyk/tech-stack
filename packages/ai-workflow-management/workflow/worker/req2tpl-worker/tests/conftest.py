"""Shared fixtures. Every test runs against a throwaway workspace root."""

from __future__ import annotations

import pytest

from app.layer4_frameworks.config import settings


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    """Point the worker's workspace at a tmp dir for the duration of a test.

    Autouse: a test that forgets this would otherwise create directories under
    the developer's real ~/.req2tpl-worker.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(settings, "REQ2TPL_WORKSPACE", str(root))
    return root
