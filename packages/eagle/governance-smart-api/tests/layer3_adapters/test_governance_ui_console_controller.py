import asyncio

import pytest

from app.layer3_adapters.controllers.restful.v1 import (
    governance_ui_console_controller as controller,
)


def _read_ui_page(file_name: str) -> str:
    return (controller._UI_ROOT / file_name).read_text(encoding="utf-8")


def test_governance_upload_ui_console_returns_page_html() -> None:
    response = asyncio.run(controller.governance_upload_ui_console())

    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert response.body.decode("utf-8") == _read_ui_page("governance_ui_console.html")


def test_governance_import_ui_console_returns_page_html() -> None:
    response = asyncio.run(controller.governance_import_ui_console())

    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert response.body.decode("utf-8") == _read_ui_page(
        "governance_import_console.html"
    )


def test_router_exposes_both_ui_console_routes() -> None:
    paths = {route.path for route in controller.router.routes}

    assert "/governance/ui/upload-file" in paths
    assert "/governance/import-data" in paths


def test_ui_console_raises_when_page_asset_missing(monkeypatch, tmp_path) -> None:
    # EDGE / invalid state: the handler reads a static asset off disk with no
    # guard, so a missing UI file surfaces as FileNotFoundError.
    monkeypatch.setattr(controller, "_UI_ROOT", tmp_path)

    with pytest.raises(FileNotFoundError):
        asyncio.run(controller.governance_upload_ui_console())
