from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(include_in_schema=False)
RESOURCE_ROOT = Path(__file__).resolve().parent / "ui"
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'; object-src 'none'"
    ),
}


@router.get("/ui")
@router.get("/ui/")
def shell():
    return FileResponse(RESOURCE_ROOT / "index.html", headers=HEADERS)


@router.get("/ui/assets/app.js")
def javascript():
    return FileResponse(RESOURCE_ROOT / "app.js", media_type="text/javascript", headers=HEADERS)


@router.get("/ui/assets/style.css")
def stylesheet():
    return FileResponse(RESOURCE_ROOT / "style.css", media_type="text/css", headers=HEADERS)
