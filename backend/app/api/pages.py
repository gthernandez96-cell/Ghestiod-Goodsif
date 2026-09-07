import sys
import os

# Auto-resolución de PYTHONPATH cuando el usuario ejecuta directamente pages.py en el IDE
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_current_dir, "../../"))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import uuid

from app.core.database import get_db
from app.models.sale import SaleOrder
from app.models.cashier import CashierSession
from app.models.company import Company, Branch
from app.models.user import User
from app.models.fel import FELDocument
from app.models.website import WebsiteConfig, get_default_website_dict

router = APIRouter()

# Directorio absoluto de templates (backend/app/templates)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../templates"))

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def redirect_to_home(request: Request, db: AsyncSession = Depends(get_db)):
    return await login_page(request, db)


@router.get("/logout", response_class=HTMLResponse)
async def logout_page(request: Request):
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token", path="/")
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        stmt = select(WebsiteConfig).order_by(WebsiteConfig.created_at.asc())
        res = await db.execute(stmt)
        config_obj = res.scalars().first()
        web_config = config_obj.to_dict() if config_obj else get_default_website_dict()
    except Exception:
        web_config = get_default_website_dict()
    return templates.TemplateResponse(request=request, name="auth/login.html", context={"web_config": web_config})


@router.get("/website-admin", response_class=HTMLResponse)
@router.get("/settings/website", response_class=HTMLResponse)
async def website_admin_page(request: Request):
    return templates.TemplateResponse(request=request, name="website/admin.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard/index.html")


@router.get("/pos", response_class=HTMLResponse)
@router.get("/pos/", response_class=HTMLResponse)
async def pos_page(request: Request):
    return templates.TemplateResponse(request=request, name="pos/index.html")


@router.get("/pos/sessions", response_class=HTMLResponse)
@router.get("/pos/sessions/", response_class=HTMLResponse)
@router.get("/sessions", response_class=HTMLResponse)
@router.get("/sessions/", response_class=HTMLResponse)
async def pos_sessions_page(request: Request):
    return templates.TemplateResponse(request=request, name="pos/sessions.html")


@router.get("/inventory", response_class=HTMLResponse)
@router.get("/inventory/", response_class=HTMLResponse)
async def inventory_page(request: Request):
    return templates.TemplateResponse(request=request, name="inventory/index.html")


@router.get("/inventory/new", response_class=HTMLResponse)
@router.get("/inventory/new/", response_class=HTMLResponse)
async def product_create_page(request: Request):
    return templates.TemplateResponse(request=request, name="inventory/form.html", context={"product_id": "new"})


@router.get("/inventory/{product_id}", response_class=HTMLResponse)
async def product_detail_page(product_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="inventory/form.html", context={"product_id": product_id})


@router.get("/purchases", response_class=HTMLResponse)
@router.get("/purchases/", response_class=HTMLResponse)
async def purchases_page(request: Request):
    return templates.TemplateResponse(request=request, name="purchases/index.html")


@router.get("/purchases/new", response_class=HTMLResponse)
@router.get("/purchases/new/", response_class=HTMLResponse)
async def purchase_create_page(request: Request):
    return templates.TemplateResponse(request=request, name="purchases/form.html", context={"purchase_id": "new"})


@router.get("/purchases/{purchase_id}", response_class=HTMLResponse)
async def purchase_detail_page(purchase_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="purchases/form.html", context={"purchase_id": purchase_id})


@router.get("/transfers", response_class=HTMLResponse)
@router.get("/transfers/", response_class=HTMLResponse)
async def transfers_page(request: Request):
    return templates.TemplateResponse(request=request, name="transfers/index.html")


@router.get("/transfers/new", response_class=HTMLResponse)
@router.get("/transfers/new/", response_class=HTMLResponse)
async def transfer_create_page(request: Request):
    return templates.TemplateResponse(request=request, name="transfers/form.html", context={"transfer_id": "new"})


@router.get("/transfers/{transfer_id}", response_class=HTMLResponse)
async def transfer_detail_page(transfer_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="transfers/form.html", context={"transfer_id": transfer_id})


@router.get("/warehouses", response_class=HTMLResponse)
@router.get("/warehouses/", response_class=HTMLResponse)
async def warehouses_page(request: Request):
    return templates.TemplateResponse(request=request, name="warehouses/index.html")


@router.get("/warehouses/new", response_class=HTMLResponse)
@router.get("/warehouses/new/", response_class=HTMLResponse)
async def warehouse_create_page(request: Request):
    return templates.TemplateResponse(request=request, name="warehouses/form.html", context={"warehouse_id": "new"})


@router.get("/warehouses/{warehouse_id}", response_class=HTMLResponse)
async def warehouse_detail_page(warehouse_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="warehouses/form.html", context={"warehouse_id": warehouse_id})


@router.get("/companies", response_class=HTMLResponse)
@router.get("/companies/", response_class=HTMLResponse)
async def companies_page(request: Request):
    return templates.TemplateResponse(request=request, name="companies/index.html")


@router.get("/customers", response_class=HTMLResponse)
@router.get("/customers/", response_class=HTMLResponse)
async def customers_page(request: Request):
    return templates.TemplateResponse(request=request, name="customers/index.html")


@router.get("/customers/new", response_class=HTMLResponse)
@router.get("/customers/new/", response_class=HTMLResponse)
async def customer_create_page(request: Request):
    return templates.TemplateResponse(request=request, name="customers/form.html", context={"partner_id": "new"})


@router.get("/customers/{partner_id}", response_class=HTMLResponse)
async def customer_detail_page(partner_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="customers/form.html", context={"partner_id": partner_id})


@router.get("/reports", response_class=HTMLResponse)
@router.get("/reports/", response_class=HTMLResponse)
async def reports_page(request: Request):
    return templates.TemplateResponse(request=request, name="reports/index.html")


@router.get("/settings", response_class=HTMLResponse)
@router.get("/settings/", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request=request, name="settings/index.html")


@router.get("/mfg", response_class=HTMLResponse)
@router.get("/mfg/", response_class=HTMLResponse)
async def mfg_page(request: Request):
    return templates.TemplateResponse(request=request, name="mfg/index.html")


@router.get("/accounting", response_class=HTMLResponse)
@router.get("/accounting/", response_class=HTMLResponse)
async def accounting_page(request: Request):
    return templates.TemplateResponse(request=request, name="accounting/index.html")


@router.get("/invoicing", response_class=HTMLResponse)
@router.get("/invoicing/", response_class=HTMLResponse)
async def invoicing_page(request: Request):
    return templates.TemplateResponse(request=request, name="invoicing/index.html")


@router.get("/hr", response_class=HTMLResponse)
@router.get("/hr/", response_class=HTMLResponse)
async def hr_page(request: Request):
    return templates.TemplateResponse(request=request, name="hr/index.html")


@router.get("/audit", response_class=HTMLResponse)
@router.get("/audit/", response_class=HTMLResponse)
async def audit_page(request: Request):
    return templates.TemplateResponse(request=request, name="audit/index.html")


@router.get("/quotes", response_class=HTMLResponse)
@router.get("/quotes/", response_class=HTMLResponse)
async def quotes_page(request: Request):
    return templates.TemplateResponse(request=request, name="quotes/index.html")


@router.get("/quotes/new", response_class=HTMLResponse)
@router.get("/quotes/new/", response_class=HTMLResponse)
async def quote_create_page(request: Request):
    return templates.TemplateResponse(request=request, name="quotes/form.html", context={"quote_id": "new"})


@router.get("/quotes/{quote_id}", response_class=HTMLResponse)
async def quote_detail_page(quote_id: str, request: Request):
    return templates.TemplateResponse(request=request, name="quotes/form.html", context={"quote_id": quote_id})


@router.get("/ap", response_class=HTMLResponse)
@router.get("/ap/", response_class=HTMLResponse)
async def ap_page(request: Request):
    return templates.TemplateResponse(request=request, name="ap/index.html")


@router.get("/ar", response_class=HTMLResponse)
@router.get("/ar/", response_class=HTMLResponse)
@router.get("/receivables", response_class=HTMLResponse)
@router.get("/receivables/", response_class=HTMLResponse)
async def ar_page(request: Request):
    return templates.TemplateResponse(request=request, name="ar/index.html")


@router.get("/inventory/labels", response_class=HTMLResponse)
@router.get("/inventory/labels/", response_class=HTMLResponse)
async def inventory_labels_page(request: Request):
    return templates.TemplateResponse(request=request, name="inventory/labels.html")


@router.get("/delivery", response_class=HTMLResponse)
@router.get("/delivery/", response_class=HTMLResponse)
async def delivery_page(request: Request):
    return templates.TemplateResponse(request=request, name="delivery/index.html")


@router.get("/users", response_class=HTMLResponse)
@router.get("/users/", response_class=HTMLResponse)
async def users_page(request: Request):
    return templates.TemplateResponse(request=request, name="users/index.html")


@router.get("/catalog", response_class=HTMLResponse)
@router.get("/catalog/", response_class=HTMLResponse)
@router.get("/menu", response_class=HTMLResponse)
@router.get("/menu/", response_class=HTMLResponse)
async def catalog_page(request: Request):
    return templates.TemplateResponse(request=request, name="catalog/index.html")


# ===== TICKETS & IMPRESIÓN =====
@router.get("/sales/{sale_id}/ticket", response_class=HTMLResponse)
async def sale_ticket_page(
    sale_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Renderizar ticket térmico 80mm de una venta."""
    try:
        s_uuid = uuid.UUID(sale_id)
    except ValueError:
        return HTMLResponse("ID de venta inválido", status_code=400)

    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines), selectinload(SaleOrder.payments), selectinload(SaleOrder.branch))
        .where(SaleOrder.id == s_uuid)
    )
    res = await db.execute(stmt)
    sale = res.scalar_one_or_none()

    if not sale:
        # Fallback dummy sale if testing with generated number
        dummy_sale = {
            "number": sale_id,
            "sale_date": None,
            "customer_name": "CONSUMIDOR FINAL",
            "customer_nit": "CF",
            "lines": [],
            "subtotal": 0.0,
            "tax_amount": 0.0,
            "total": 0.0,
            "payments": [],
        }
        dummy_company = {"name": "POS EMPRESARIAL", "nit": "12345678-9"}
        dummy_branch = {"name": "Sucursal Central", "address": "Guatemala"}
        return templates.TemplateResponse(
            request=request,
            name="pos/ticket_receipt.html",
            context={"sale": dummy_sale, "company": dummy_company, "branch": dummy_branch, "fel": None, "hasattr": hasattr}
        )

    # Fetch company and branch
    comp_res = await db.execute(select(Company).where(Company.id == sale.company_id))
    company = comp_res.scalar_one_or_none()

    fel_res = await db.execute(select(FELDocument).where(FELDocument.sale_id == sale.id))
    fel = fel_res.scalar_one_or_none()

    return templates.TemplateResponse(
        request=request,
        name="pos/ticket_receipt.html",
        context={
            "sale": sale,
            "company": company,
            "branch": sale.branch,
            "fel": fel,
            "hasattr": hasattr
        }
    )


@router.get("/pos/z-report", response_class=HTMLResponse)
@router.get("/pos/z-report/", response_class=HTMLResponse)
async def pos_latest_z_report(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Renderizar el reporte de Cierre Z más reciente o de la sesión activa."""
    stmt = (
        select(CashierSession)
        .options(selectinload(CashierSession.user), selectinload(CashierSession.branch))
        .order_by(CashierSession.opened_at.desc())
        .limit(1)
    )
    res = await db.execute(stmt)
    session = res.scalar_one_or_none()

    comp_res = await db.execute(select(Company).limit(1))
    company = comp_res.scalar_one_or_none()

    if session:
        from app.models.sale import SaleOrder, SalePayment, SaleState
        from sqlalchemy import func

        pay_stmt = (
            select(SalePayment.payment_method, func.sum(SalePayment.amount))
            .join(SaleOrder, SalePayment.sale_id == SaleOrder.id)
            .where(SaleOrder.cashier_session_id == session.id, SaleOrder.state != SaleState.CANCELLED)
            .group_by(SalePayment.payment_method)
        )
        pay_res = await db.execute(pay_stmt)
        pay_map = {m: float(a or 0) for m, a in pay_res.all()}

        cash_sales = pay_map.get("cash", float(session.total_sales or 0))
        card_sales = pay_map.get("card", 0.0) + pay_map.get("transfer", 0.0)
        credit_sales = pay_map.get("credit", 0.0)

        due_stmt = select(func.sum(SaleOrder.amount_due)).where(
            SaleOrder.cashier_session_id == session.id,
            SaleOrder.state != SaleState.CANCELLED
        )
        due_res = await db.execute(due_stmt)
        due_val = float(due_res.scalar() or 0.0)
        if due_val > credit_sales:
            credit_sales = due_val

        total_sales = cash_sales + card_sales + credit_sales
        opening = float(session.opening_balance or 0)
        cash_in = float(session.total_cash_in or 0)
        cash_out = float(session.total_cash_out or 0)
        expected_cash = opening + cash_in + cash_sales - cash_out

        return templates.TemplateResponse(
            request=request,
            name="pos/z_report.html",
            context={
                "session": session,
                "user": session.user,
                "branch": session.branch,
                "company": company,
                "cash_sales": cash_sales,
                "credit_sales": credit_sales,
                "card_sales": card_sales,
                "total_sales": total_sales,
                "expected_cash": expected_cash,
            }
        )
    return RedirectResponse(url="/pos/sessions")


@router.get("/cashier/sessions/{session_id}/z-report", response_class=HTMLResponse)
async def cashier_z_report(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Renderizar el reporte de Cierre Z de una sesión de caja."""
    try:
        s_uuid = uuid.UUID(session_id)
    except ValueError:
        return HTMLResponse("ID de sesión inválido", status_code=400)

    stmt = (
        select(CashierSession)
        .options(selectinload(CashierSession.user), selectinload(CashierSession.branch))
        .where(CashierSession.id == s_uuid)
    )
    res = await db.execute(stmt)
    session = res.scalar_one_or_none()

    if not session:
        return HTMLResponse("Sesión no encontrada", status_code=404)

    comp_res = await db.execute(select(Company).limit(1))
    company = comp_res.scalar_one_or_none()

    from app.models.sale import SaleOrder, SalePayment, SaleState
    from sqlalchemy import func

    pay_stmt = (
        select(SalePayment.payment_method, func.sum(SalePayment.amount))
        .join(SaleOrder, SalePayment.sale_id == SaleOrder.id)
        .where(SaleOrder.cashier_session_id == session.id, SaleOrder.state != SaleState.CANCELLED)
        .group_by(SalePayment.payment_method)
    )
    pay_res = await db.execute(pay_stmt)
    pay_map = {m: float(a or 0) for m, a in pay_res.all()}

    cash_sales = pay_map.get("cash", float(session.total_sales or 0))
    card_sales = pay_map.get("card", 0.0) + pay_map.get("transfer", 0.0)
    credit_sales = pay_map.get("credit", 0.0)

    due_stmt = select(func.sum(SaleOrder.amount_due)).where(
        SaleOrder.cashier_session_id == session.id,
        SaleOrder.state != SaleState.CANCELLED
    )
    due_res = await db.execute(due_stmt)
    due_val = float(due_res.scalar() or 0.0)
    if due_val > credit_sales:
        credit_sales = due_val

    total_sales = cash_sales + card_sales + credit_sales
    opening = float(session.opening_balance or 0)
    cash_in = float(session.total_cash_in or 0)
    cash_out = float(session.total_cash_out or 0)
    expected_cash = opening + cash_in + cash_sales - cash_out

    return templates.TemplateResponse(
        request=request,
        name="pos/z_report.html",
        context={
            "session": session,
            "user": session.user,
            "branch": session.branch,
            "company": company,
            "cash_sales": cash_sales,
            "credit_sales": credit_sales,
            "card_sales": card_sales,
            "total_sales": total_sales,
            "expected_cash": expected_cash,
        }
    )


# ===== PWA SUPPORT ENDPOINTS =====
@router.get("/service-worker.js")
async def service_worker():
    """Servir el archivo Service Worker desde la raíz para control global de alcance (scope)."""
    sw_path = os.path.abspath(os.path.join(CURRENT_DIR, "../../../frontend/static/js/sw.js"))
    if os.path.exists(sw_path):
        return FileResponse(sw_path, media_type="application/javascript")
    return HTMLResponse("Service Worker not found", status_code=404)


@router.get("/manifest.json")
async def web_manifest():
    """Servir el manifiesto de la PWA."""
    manifest_path = os.path.abspath(os.path.join(CURRENT_DIR, "../../../frontend/static/manifest.json"))
    if os.path.exists(manifest_path):
        return FileResponse(manifest_path, media_type="application/json")
    return HTMLResponse("Manifest not found", status_code=404)


if __name__ == "__main__":
    import uvicorn
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./pos.db")
    os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///./pos.db")
    os.environ.setdefault("SECRET_KEY", "clave-secreta-pos-2024")
    print("🚀 Iniciando POS Empresarial desde pages.py...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
