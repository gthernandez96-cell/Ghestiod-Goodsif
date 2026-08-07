"""Router de páginas HTML — Frontend Jinja2."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os

router = APIRouter()

# Directorio absoluto de templates (backend/app/templates)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../templates"))

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def redirect_to_login(request: Request):
    return RedirectResponse(url="/login")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="auth/login.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard/index.html")


@router.get("/pos", response_class=HTMLResponse)
async def pos_page(request: Request):
    return templates.TemplateResponse(request=request, name="pos/index.html")


@router.get("/inventory", response_class=HTMLResponse)
async def inventory_page(request: Request):
    return templates.TemplateResponse(request=request, name="inventory/index.html")


@router.get("/purchases", response_class=HTMLResponse)
async def purchases_page(request: Request):
    return templates.TemplateResponse(request=request, name="purchases/index.html")


@router.get("/transfers", response_class=HTMLResponse)
async def transfers_page(request: Request):
    return templates.TemplateResponse(request=request, name="transfers/index.html")


@router.get("/customers", response_class=HTMLResponse)
async def customers_page(request: Request):
    return templates.TemplateResponse(request=request, name="customers/index.html")


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    return templates.TemplateResponse(request=request, name="reports/index.html")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request=request, name="settings/index.html")
