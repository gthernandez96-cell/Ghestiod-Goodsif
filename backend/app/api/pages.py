"""Router de páginas HTML — Frontend Jinja2."""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

router = APIRouter()

TEMPLATES_DIR = "/app/app/templates" if os.path.exists("/app/app/templates") else "app/templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _ctx(request: Request, **kwargs) -> dict:
    return {"request": request, **kwargs}


@router.get("/", response_class=HTMLResponse)
async def redirect_to_login(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/login")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", _ctx(request))


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard/index.html", _ctx(request))


@router.get("/pos", response_class=HTMLResponse)
async def pos_page(request: Request):
    return templates.TemplateResponse("pos/index.html", _ctx(request))


@router.get("/inventory", response_class=HTMLResponse)
async def inventory_page(request: Request):
    return templates.TemplateResponse("inventory/index.html", _ctx(request))


@router.get("/purchases", response_class=HTMLResponse)
async def purchases_page(request: Request):
    return templates.TemplateResponse("purchases/index.html", _ctx(request))


@router.get("/transfers", response_class=HTMLResponse)
async def transfers_page(request: Request):
    return templates.TemplateResponse("transfers/index.html", _ctx(request))


@router.get("/customers", response_class=HTMLResponse)
async def customers_page(request: Request):
    return templates.TemplateResponse("customers/index.html", _ctx(request))


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    return templates.TemplateResponse("reports/index.html", _ctx(request))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings/index.html", _ctx(request))
