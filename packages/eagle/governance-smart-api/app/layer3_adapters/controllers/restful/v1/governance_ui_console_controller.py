from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["governance-ui"])

_UI_ROOT = Path(__file__).resolve().parents[4] / "layer2_application" / "uis"


@router.get("/governance/ui/upload-file", response_class=HTMLResponse)
async def governance_upload_ui_console() -> HTMLResponse:
    page_path = _UI_ROOT / "governance_ui_console.html"
    return HTMLResponse(page_path.read_text(encoding="utf-8"))


@router.get("/governance/import-data", response_class=HTMLResponse)
async def governance_import_ui_console() -> HTMLResponse:
    page_path = _UI_ROOT / "governance_import_console.html"
    return HTMLResponse(page_path.read_text(encoding="utf-8"))
