from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])

_UI_ROOT = Path(__file__).resolve().parents[4] / "layer2_application" / "uis"


def _read_ui_page(page_name: str) -> HTMLResponse:
    page_path = _UI_ROOT / page_name
    return HTMLResponse(page_path.read_text(encoding="utf-8"))


@router.get("/ui", response_class=HTMLResponse)
async def ui_console_index() -> HTMLResponse:
    return _read_ui_page("index.html")


@router.get("/ui/search", response_class=HTMLResponse)
async def ui_console_search() -> HTMLResponse:
    return _read_ui_page("search.html")


@router.get("/ui/similarity", response_class=HTMLResponse)
async def ui_console_similarity() -> HTMLResponse:
    return _read_ui_page("similarity.html")


@router.get("/ui/config", response_class=HTMLResponse)
async def ui_console_config() -> HTMLResponse:
    return _read_ui_page("config.html")


@router.get("/ui/material-sds-analysis", response_class=HTMLResponse)
async def ui_console_material_sds_analysis() -> HTMLResponse:
    return _read_ui_page("material_sds_analysis.html")


@router.get("/ui/material-group-recommendation", response_class=HTMLResponse)
async def ui_console_material_group_recommendation() -> HTMLResponse:
    return _read_ui_page("material_group_recommendation.html")


@router.get("/ui/buyer-recommendation", response_class=HTMLResponse)
async def ui_console_buyer_recommendation() -> HTMLResponse:
    return _read_ui_page("buyer_recommendation.html")
