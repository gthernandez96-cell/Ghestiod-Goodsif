"""API v1 — Dashboard y Métricas de Negocio en Tiempo Real."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, cast, Date
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SalePayment, SaleState
from app.models.cashier import CashierSession, SessionState
from app.models.transfer import Transfer, TransferState
from app.models.product import Product, ProductCategory
from app.models.inventory import StockQuant
from app.models.ap import SupplierBill
from app.models.ar import CustomerInvoiceCredit
from app.models.company import Branch, Warehouse

from app.models.user import User

router = APIRouter(prefix="/dashboard")

try:
    from zoneinfo import ZoneInfo
    GUATEMALA_TZ = ZoneInfo("America/Guatemala")
except Exception:
    GUATEMALA_TZ = timezone(timedelta(hours=-6))


def to_local_dt(dt: Optional[datetime], tz=GUATEMALA_TZ) -> Optional[datetime]:
    """Convierte un datetime (almacenado en UTC en la BD) a la zona horaria local (Guatemala UTC-6)."""
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def to_utc_naive(dt: datetime) -> datetime:
    """Convierte un datetime a naive UTC para filtros compatibles con SQLite y PostgreSQL."""
    if not dt:
        return dt
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def resolve_period_dates(
    period: str = "month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[datetime, datetime, datetime, datetime, str]:
    """Calcula rango de fechas en UTC (inicio, fin, previo_inicio, previo_fin, etiqueta) según el huso horario local de Guatemala."""
    now_local = datetime.now(GUATEMALA_TZ)
    today_local = now_local.date()
    start_of_today_local = datetime.combine(today_local, datetime.min.time(), tzinfo=GUATEMALA_TZ)
    start_of_yesterday_local = start_of_today_local - timedelta(days=1)
    start_of_month_local = datetime.combine(today_local.replace(day=1), datetime.min.time(), tzinfo=GUATEMALA_TZ)

    if period == "today":
        p_start_loc = start_of_today_local
        p_end_loc = datetime.combine(today_local, datetime.max.time(), tzinfo=GUATEMALA_TZ)
        prev_start_loc = start_of_yesterday_local
        prev_end_loc = datetime.combine(today_local - timedelta(days=1), datetime.max.time(), tzinfo=GUATEMALA_TZ)
        period_label = "Hoy"
    elif period == "week":
        p_start_loc = datetime.combine(today_local - timedelta(days=today_local.weekday()), datetime.min.time(), tzinfo=GUATEMALA_TZ)
        p_end_loc = datetime.combine(today_local, datetime.max.time(), tzinfo=GUATEMALA_TZ)
        duration = p_end_loc - p_start_loc
        prev_start_loc = p_start_loc - duration
        prev_end_loc = p_start_loc
        period_label = "Esta Semana"
    elif period == "year":
        p_start_loc = datetime.combine(today_local.replace(month=1, day=1), datetime.min.time(), tzinfo=GUATEMALA_TZ)
        p_end_loc = datetime.combine(today_local, datetime.max.time(), tzinfo=GUATEMALA_TZ)
        prev_start_loc = datetime.combine(today_local.replace(year=today_local.year - 1, month=1, day=1), datetime.min.time(), tzinfo=GUATEMALA_TZ)
        prev_end_loc = datetime.combine(today_local.replace(year=today_local.year - 1, month=today_local.month, day=today_local.day), datetime.max.time(), tzinfo=GUATEMALA_TZ)
        period_label = "Este Año"
    elif period == "custom" and start_date and end_date:
        try:
            p_start_loc = datetime.combine(datetime.strptime(start_date[:10], "%Y-%m-%d").date(), datetime.min.time(), tzinfo=GUATEMALA_TZ)
            p_end_loc = datetime.combine(datetime.strptime(end_date[:10], "%Y-%m-%d").date(), datetime.max.time(), tzinfo=GUATEMALA_TZ)
            duration = p_end_loc - p_start_loc
            prev_start_loc = p_start_loc - duration
            prev_end_loc = p_start_loc
            period_label = f"{start_date[:10]} al {end_date[:10]}"
        except Exception:
            p_start_loc = start_of_month_local
            p_end_loc = datetime.combine(today_local, datetime.max.time(), tzinfo=GUATEMALA_TZ)
            prev_month_last = today_local.replace(day=1) - timedelta(days=1)
            prev_start_loc = datetime.combine(prev_month_last.replace(day=1), datetime.min.time(), tzinfo=GUATEMALA_TZ)
            prev_end_loc = start_of_month_local
            period_label = "Este Mes"
    else:  # "month" por defecto
        p_start_loc = start_of_month_local
        p_end_loc = datetime.combine(today_local, datetime.max.time(), tzinfo=GUATEMALA_TZ)
        prev_month_last = today_local.replace(day=1) - timedelta(days=1)
        prev_start_loc = datetime.combine(prev_month_last.replace(day=1), datetime.min.time(), tzinfo=GUATEMALA_TZ)
        prev_end_loc = start_of_month_local
        period_label = "Este Mes"

    p_start = to_utc_naive(p_start_loc)
    p_end = to_utc_naive(p_end_loc)
    prev_start = to_utc_naive(prev_start_loc)
    prev_end = to_utc_naive(prev_end_loc)

    return p_start, p_end, prev_start, prev_end, period_label


@router.get("/kpis")
async def get_kpis(
    period: str = Query("today"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener resumen de KPIs en tiempo real con filtro temporal dinámico y cálculo de utilidades."""
    p_start, p_end, prev_start, prev_end, period_label = resolve_period_dates(period, start_date, end_date)
    now_local = datetime.now(GUATEMALA_TZ)
    today_local = now_local.date()
    start_of_today = to_utc_naive(datetime.combine(today_local, datetime.min.time(), tzinfo=GUATEMALA_TZ))
    start_of_month = to_utc_naive(datetime.combine(today_local.replace(day=1), datetime.min.time(), tzinfo=GUATEMALA_TZ))

    # 1. Ventas y Facturas del Período
    sales_period_stmt = select(
        func.coalesce(func.sum(SaleOrder.total), Decimal("0")).label("total_sales"),
        func.count(SaleOrder.id).label("total_invoices")
    ).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= p_start,
        SaleOrder.sale_date <= p_end,
        SaleOrder.state != SaleState.CANCELLED
    )

    # 1.1 Ventas del Período Anterior (para calcular % de crecimiento)
    sales_prev_stmt = select(
        func.coalesce(func.sum(SaleOrder.total), Decimal("0")).label("total_sales"),
        func.count(SaleOrder.id).label("total_invoices")
    ).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= prev_start,
        SaleOrder.sale_date < prev_end,
        SaleOrder.state != SaleState.CANCELLED
    )

    # 1.2 Utilidad Bruta del Período (Ventas - Costos COGS)
    profit_stmt = select(
        func.coalesce(func.sum(SaleOrderLine.subtotal), Decimal("0")),
        func.coalesce(func.sum(SaleOrderLine.qty * func.coalesce(SaleOrderLine.cost_price, Decimal("0"))), Decimal("0"))
    ).join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= p_start,
        SaleOrder.sale_date <= p_end,
        SaleOrder.state != SaleState.CANCELLED
    )

    # 2. Ventas y Facturas Hoy (Siempre disponibles)
    sales_today_stmt = select(
        func.coalesce(func.sum(SaleOrder.total), Decimal("0")).label("total_sales"),
        func.count(SaleOrder.id).label("total_invoices")
    ).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= start_of_today,
        SaleOrder.state != SaleState.CANCELLED
    )

    # 3. Ventas del Mes
    sales_month_stmt = select(
        func.coalesce(func.sum(SaleOrder.total), Decimal("0")),
        func.count(SaleOrder.id)
    ).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= start_of_month,
        SaleOrder.state != SaleState.CANCELLED
    )

    # 4. Cajas abiertas
    sessions_stmt = select(func.count(CashierSession.id)).where(
        CashierSession.state == SessionState.OPEN
    )

    # 5. Traslados pendientes
    transfers_stmt = select(func.count(Transfer.id)).where(
        Transfer.company_id == current_user.company_id,
        Transfer.state.in_([TransferState.CONFIRMED, TransferState.SENT, TransferState.IN_TRANSIT])
    )

    # 6. Cuentas por Cobrar (AR) vs Cuentas por Pagar (AP)
    ar_stmt = select(func.coalesce(func.sum(CustomerInvoiceCredit.balance), Decimal("0"))).where(
        CustomerInvoiceCredit.company_id == current_user.company_id,
        CustomerInvoiceCredit.balance > 0,
        CustomerInvoiceCredit.is_deleted == False
    )
    ap_stmt = select(func.coalesce(func.sum(SupplierBill.balance), Decimal("0"))).where(
        SupplierBill.company_id == current_user.company_id,
        SupplierBill.balance > 0,
        SupplierBill.is_deleted == False
    )

    if branch_id:
        try:
            b_uuid = uuid.UUID(branch_id)
            sales_period_stmt = sales_period_stmt.where(SaleOrder.branch_id == b_uuid)
            sales_prev_stmt = sales_prev_stmt.where(SaleOrder.branch_id == b_uuid)
            profit_stmt = profit_stmt.where(SaleOrder.branch_id == b_uuid)
            sales_today_stmt = sales_today_stmt.where(SaleOrder.branch_id == b_uuid)
            sales_month_stmt = sales_month_stmt.where(SaleOrder.branch_id == b_uuid)
            sessions_stmt = sessions_stmt.where(CashierSession.branch_id == b_uuid)
        except ValueError:
            pass

    # Ejecución de consultas
    p_res = await db.execute(sales_period_stmt)
    p_row = p_res.first()
    sales_period = float(p_row.total_sales or 0) if p_row else 0.0
    invoices_period = int(p_row.total_invoices or 0) if p_row else 0

    prev_res = await db.execute(sales_prev_stmt)
    prev_row = prev_res.first()
    sales_prev = float(prev_row.total_sales or 0) if prev_row else 0.0
    invoices_prev = int(prev_row.total_invoices or 0) if prev_row else 0

    prof_res = await db.execute(profit_stmt)
    prof_row = prof_res.first()
    revenue_val = float(prof_row[0] or 0) if prof_row else 0.0
    cogs_val = float(prof_row[1] or 0) if prof_row else 0.0
    gross_profit = max(0.0, revenue_val - cogs_val)
    gross_margin_pct = round((gross_profit / revenue_val) * 100, 1) if revenue_val > 0 else 0.0

    st_res = await db.execute(sales_today_stmt)
    st_row = st_res.first()
    sales_today = float(st_row.total_sales or 0) if st_row else 0.0
    invoices_today = int(st_row.total_invoices or 0) if st_row else 0

    sm_res = await db.execute(sales_month_stmt)
    sm_row = sm_res.first()
    sales_month = float(sm_row[0] or 0) if sm_row else 0.0
    invoices_month = int(sm_row[1] or 0) if sm_row else 0

    sess_res = await db.execute(sessions_stmt)
    active_sessions = int(sess_res.scalar() or 0)

    tr_res = await db.execute(transfers_stmt)
    pending_transfers = int(tr_res.scalar() or 0)

    ar_res = await db.execute(ar_stmt)
    total_receivable = float(ar_res.scalar() or 0.0)

    ap_res = await db.execute(ap_stmt)
    total_payable = float(ap_res.scalar() or 0.0)

    # 7. Productos Stock Bajo (optimizado en una sola consulta agregada)
    low_stock_subquery = (
        select(
            StockQuant.product_id,
            func.coalesce(func.sum(StockQuant.qty_on_hand), 0).label("total_q")
        )
        .group_by(StockQuant.product_id)
        .subquery()
    )
    low_stock_stmt = (
        select(func.count(Product.id))
        .outerjoin(low_stock_subquery, Product.id == low_stock_subquery.c.product_id)
        .where(
            Product.company_id == current_user.company_id,
            Product.is_active == True,
            Product.min_stock > 0,
            func.coalesce(low_stock_subquery.c.total_q, 0) <= Product.min_stock
        )
    )
    low_res = await db.execute(low_stock_stmt)
    low_stock_count = int(low_res.scalar() or 0)

    # Tickets promedio
    avg_ticket_period = round(sales_period / invoices_period, 2) if invoices_period > 0 else 0.0
    avg_ticket_today = round(sales_today / invoices_today, 2) if invoices_today > 0 else 0.0
    avg_ticket_month = round(sales_month / invoices_month, 2) if invoices_month > 0 else 0.0

    # Tendencias (% crecimiento vs período anterior)
    if sales_prev > 0:
        sales_growth_pct = round(((sales_period - sales_prev) / sales_prev) * 100, 1)
    else:
        sales_growth_pct = 100.0 if sales_period > 0 else 0.0

    if invoices_prev > 0:
        invoices_growth_pct = round(((invoices_period - invoices_prev) / invoices_prev) * 100, 1)
    else:
        invoices_growth_pct = 100.0 if invoices_period > 0 else 0.0

    return {
        "period": period,
        "period_label": period_label,
        "sales_period": sales_period,
        "invoices_period": invoices_period,
        "avg_ticket_period": avg_ticket_period,
        "gross_profit": gross_profit,
        "gross_margin_pct": gross_margin_pct,
        "sales_growth_pct": sales_growth_pct,
        "invoices_growth_pct": invoices_growth_pct,
        "sales_today": sales_today,
        "invoices_today": invoices_today,
        "avg_ticket_today": avg_ticket_today,
        "sales_month": sales_month,
        "invoices_month": invoices_month,
        "avg_ticket_month": avg_ticket_month,
        "low_stock": low_stock_count,
        "active_sessions": active_sessions,
        "pending_transfers": pending_transfers,
        "total_receivable": total_receivable,
        "total_payable": total_payable,
        "sales_trend": sales_growth_pct,
        "invoices_trend": invoices_growth_pct,
    }


@router.get("/cashier-performance")
async def get_cashier_performance(
    period: str = Query("month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener rendimiento y ranking de ventas por cajero/vendedor."""
    p_start, p_end, _, _, _ = resolve_period_dates(period, start_date, end_date)

    stmt = (
        select(
            User.id,
            User.full_name,
            User.username,
            User.email,
            func.count(SaleOrder.id).label("total_tickets"),
            func.coalesce(func.sum(SaleOrder.total), Decimal("0")).label("total_sales"),
            func.coalesce(func.avg(SaleOrder.total), Decimal("0")).label("avg_ticket"),
            func.min(SaleOrder.sale_date).label("first_sale"),
            func.max(SaleOrder.sale_date).label("last_sale")
        )
        .join(SaleOrder, SaleOrder.user_id == User.id)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= p_start,
            SaleOrder.sale_date <= p_end,
            SaleOrder.state != SaleState.CANCELLED
        )
        .group_by(User.id, User.full_name, User.username, User.email)
        .order_by(func.sum(SaleOrder.total).desc())
    )

    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    rows = res.all()

    total_sales_all = sum(float(r.total_sales or 0) for r in rows) or 1.0

    items = []
    for rank, r in enumerate(rows, start=1):
        s_val = float(r.total_sales or 0)
        pct = round((s_val / total_sales_all) * 100, 1)
        items.append({
            "rank": rank,
            "user_id": str(r.id),
            "name": getattr(r, "full_name", None) or r.username or "Colaborador",
            "username": r.username,
            "email": r.email,
            "tickets": int(r.total_tickets or 0),
            "total_sales": s_val,
            "avg_ticket": round(float(r.avg_ticket or 0), 2),
            "share_pct": pct,
            "last_active": r.last_sale.strftime("%d/%m %H:%M") if r.last_sale else "N/A"
        })

    return {"period": period, "total_sellers": len(items), "items": items}


@router.get("/export-excel")
async def export_bi_excel(
    period: str = Query("month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar informe ejecutivo consolidado en formato CSV/Excel."""
    import csv
    import io
    from fastapi.responses import Response

    now = datetime.now()
    today = now.date()
    p_start, p_end, _, _, period_label = resolve_period_dates(period, start_date, end_date)

    # Ventas del período
    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines))
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= p_start,
            SaleOrder.sale_date <= p_end,
            SaleOrder.state != SaleState.CANCELLED
        )
        .order_by(SaleOrder.sale_date.desc())
    )
    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    sales = res.scalars().all()

    branch_title = "Todas las Sucursales"
    if branch_id:
        try:
            b_stmt = select(Branch).where(Branch.id == uuid.UUID(branch_id))
            b_res = await db.execute(b_stmt)
            b_obj = b_res.scalar_one_or_none()
            if b_obj:
                branch_title = b_obj.name
        except Exception:
            pass

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "REPORTE EJECUTIVO DE VENTAS Y BI — POS EMPRESARIAL",
        f"Generado: {now.strftime('%d/%m/%Y %H:%M')}",
        f"Período: {period.upper()}",
        f"Sucursal: {branch_title}"
    ])
    writer.writerow([])
    writer.writerow([
        "No. Factura", "Fecha y Hora", "Cliente", "NIT", "Tipo", "Subtotal (Q)", "Total (Q)", "Estado"
    ])

    total_amount = 0.0
    for s in sales:
        total_amount += float(s.total or 0)
        loc_dt = to_local_dt(s.sale_date)
        s_date_str = loc_dt.strftime("%d/%m/%Y %H:%M") if loc_dt else ""
        writer.writerow([
            s.number,
            s_date_str,
            s.customer_name or "CONSUMIDOR FINAL",
            s.customer_nit or "CF",
            s.sale_type.value if hasattr(s.sale_type, 'value') else str(s.sale_type),
            f"{float(s.subtotal or 0):.2f}",
            f"{float(s.total or 0):.2f}",
            s.state.value if hasattr(s.state, 'value') else str(s.state)
        ])

    writer.writerow([])
    writer.writerow(["TOTAL FACTURADO", "", "", "", "", "", f"{total_amount:.2f}", ""])

    csv_data = "\ufeff" + output.getvalue()
    filename = f"reporte_bi_{period}_{today.strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/sales-chart")
async def get_sales_chart(
    period: Optional[str] = Query(None),
    days: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener serie temporal de ventas continuas por día u hora según el período o días seleccionados en hora local."""
    now_local = datetime.now(GUATEMALA_TZ)
    today_local = now_local.date()

    if period:
        p_start, p_end, _, _, _ = resolve_period_dates(period, start_date, end_date)
    else:
        num_days = days or 30
        p_start_loc = datetime.combine(today_local - timedelta(days=num_days - 1), datetime.min.time(), tzinfo=GUATEMALA_TZ)
        p_end_loc = datetime.combine(today_local, datetime.max.time(), tzinfo=GUATEMALA_TZ)
        p_start = to_utc_naive(p_start_loc)
        p_end = to_utc_naive(p_end_loc)

    # Si es "today", graficar horas del día en horario local
    if period == "today":
        hours_dict = {f"{h:02d}:00": 0.0 for h in range(0, 24)}
        counts_dict = {f"{h:02d}:00": 0 for h in range(0, 24)}
        stmt = select(SaleOrder.sale_date, SaleOrder.total).where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= p_start,
            SaleOrder.sale_date <= p_end,
            SaleOrder.state != SaleState.CANCELLED
        ).order_by(SaleOrder.sale_date.asc())
        if branch_id:
            try:
                stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
            except ValueError:
                pass
        res = await db.execute(stmt)
        has_early = False
        for s_date, total in res.all():
            loc_dt = to_local_dt(s_date)
            if loc_dt:
                h = loc_dt.hour
                h_key = f"{h:02d}:00"
                hours_dict[h_key] += float(total or 0)
                counts_dict[h_key] += 1
                if h < 6:
                    has_early = True
        display_range = range(0, 24) if has_early else range(6, 24)
        return {
            "labels": [f"{h:02d}:00" for h in display_range],
            "data": [round(hours_dict[f"{h:02d}:00"], 2) for h in display_range],
            "counts": [counts_dict[f"{h:02d}:00"] for h in display_range],
            "period": "today"
        }

    # Si el período es anual o mayor a 60 días, agrupar por mes
    total_days = max(1, (to_local_dt(p_end).date() - to_local_dt(p_start).date()).days + 1)
    if period == "year" or total_days > 90:
        months_dict = {}
        counts_dict = {}
        curr = to_local_dt(p_start).replace(day=1)
        p_end_loc = to_local_dt(p_end)
        while curr <= p_end_loc:
            m_key = curr.strftime("%Y-%m")
            months_dict[m_key] = 0.0
            counts_dict[m_key] = 0
            if curr.month == 12:
                curr = curr.replace(year=curr.year + 1, month=1)
            else:
                curr = curr.replace(month=curr.month + 1)

        stmt = select(SaleOrder.sale_date, SaleOrder.total).where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= p_start,
            SaleOrder.sale_date <= p_end,
            SaleOrder.state != SaleState.CANCELLED
        ).order_by(SaleOrder.sale_date.asc())
        if branch_id:
            try:
                stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
            except ValueError:
                pass
        res = await db.execute(stmt)
        for s_date, total in res.all():
            loc_dt = to_local_dt(s_date)
            if loc_dt:
                m_key = loc_dt.strftime("%Y-%m")
                if m_key in months_dict:
                    months_dict[m_key] += float(total or 0)
                    counts_dict[m_key] += 1
        m_names = {"01":"Ene","02":"Feb","03":"Mar","04":"Abr","05":"May","06":"Jun","07":"Jul","08":"Ago","09":"Sep","10":"Oct","11":"Nov","12":"Dic"}
        labels = [m_names.get(k[5:7], k[5:7]) for k in months_dict.keys()]
        return {
            "labels": labels,
            "data": [round(v, 2) for v in months_dict.values()],
            "counts": list(counts_dict.values()),
            "period": period or "custom"
        }

    # Días continuos para week, month, custom o num_days en fecha local
    days_dict = {}
    counts_dict = {}
    curr = to_local_dt(p_start).date()
    end_d = to_local_dt(p_end).date()
    while curr <= end_d:
        d_key = curr.strftime("%Y-%m-%d")
        days_dict[d_key] = 0.0
        counts_dict[d_key] = 0
        curr += timedelta(days=1)

    stmt = select(
        SaleOrder.sale_date,
        SaleOrder.total
    ).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= p_start,
        SaleOrder.sale_date <= p_end,
        SaleOrder.state != SaleState.CANCELLED
    ).order_by(SaleOrder.sale_date.asc())

    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    rows = res.all()

    for sale_date, total in rows:
        loc_dt = to_local_dt(sale_date)
        if not loc_dt:
            continue
        day_key = loc_dt.strftime("%Y-%m-%d")
        if day_key in days_dict:
            days_dict[day_key] += float(total or 0)
            counts_dict[day_key] += 1

    labels = []
    for k in days_dict.keys():
        try:
            dt = datetime.strptime(str(k), "%Y-%m-%d")
            labels.append(dt.strftime("%d/%m"))
        except Exception:
            labels.append(str(k))

    data = [round(v, 2) for v in days_dict.values()]
    counts = list(counts_dict.values())

    return {"labels": labels, "data": data, "counts": counts, "period": period or f"{days or 30}d"}


@router.get("/category-breakdown")
async def get_category_breakdown(
    period: str = Query("month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Distribución de ingresos por categoría de producto para gráfico Doughnut."""
    p_start, p_end, _, _, _ = resolve_period_dates(period, start_date, end_date)

    stmt = (
        select(
            func.coalesce(ProductCategory.name, "General").label("cat_name"),
            func.sum(SaleOrderLine.total).label("total_sales")
        )
        .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
        .outerjoin(Product, SaleOrderLine.product_id == Product.id)
        .outerjoin(ProductCategory, Product.category_id == ProductCategory.id)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= p_start,
            SaleOrder.sale_date <= p_end,
            SaleOrder.state != SaleState.CANCELLED
        )
        .group_by("cat_name")
        .order_by(func.sum(SaleOrderLine.total).desc())
    )

    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    rows = res.all()

    labels = []
    data = []
    for cat_name, total in rows:
        labels.append(str(cat_name))
        data.append(round(float(total or 0), 2))

    if not data:
        labels = ["Sin ventas en el período"]
        data = [0.0]

    return {"labels": labels, "data": data, "period": period}


@router.get("/payment-methods")
async def get_payment_methods_breakdown(
    period: str = Query("month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Distribución de recaudación por método de pago."""
    p_start, p_end, _, _, _ = resolve_period_dates(period, start_date, end_date)

    stmt = (
        select(
            SalePayment.payment_method,
            func.sum(SalePayment.amount).label("total_amount")
        )
        .join(SaleOrder, SalePayment.sale_id == SaleOrder.id)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= p_start,
            SaleOrder.sale_date <= p_end,
            SaleOrder.state != SaleState.CANCELLED
        )
        .group_by(SalePayment.payment_method)
    )

    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    rows = res.all()

    methods_map = {
        "cash": "Efectivo",
        "card": "Tarjeta de Crédito / Débito",
        "transfer": "Transferencia Bancaria",
        "check": "Cheque",
        "credit": "Crédito a Clientes (CxC)"
    }

    labels = []
    data = []
    for p_method, amount in rows:
        m_str = str(p_method.value if hasattr(p_method, 'value') else p_method).lower()
        label = methods_map.get(m_str, m_str.capitalize())
        labels.append(label)
        data.append(round(float(amount or 0), 2))

    if not data:
        labels = ["Sin cobros registrados"]
        data = [0.0]

    return {"labels": labels, "data": data, "period": period}

    return {"labels": labels, "data": data}


@router.get("/top-products")
async def get_top_products(
    limit: int = Query(5, le=20),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener los productos más vendidos."""
    stmt = select(
        SaleOrderLine.product_name,
        func.sum(SaleOrderLine.qty).label("total_qty"),
        func.sum(SaleOrderLine.total).label("total_sales")
    ).join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.state != SaleState.CANCELLED
    ).group_by(SaleOrderLine.product_name).order_by(func.sum(SaleOrderLine.total).desc()).limit(limit)

    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    rows = res.all()

    items = [
        {"name": name, "qty": float(qty or 0), "total": float(total or 0)}
        for name, qty, total in rows
    ]

    return items


@router.get("/recent-sales")
async def get_recent_sales(
    limit: int = Query(6, le=20),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener las últimas ventas registradas."""
    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.payments))
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state != SaleState.CANCELLED
        )
        .order_by(SaleOrder.sale_date.desc())
        .limit(limit)
    )

    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    sales = res.scalars().all()

    items = []
    for s in sales:
        pm = s.payments[0].payment_method.value if s.payments and hasattr(s.payments[0].payment_method, 'value') else "Efectivo"
        loc_dt = to_local_dt(s.sale_date)
        items.append({
            "id": str(s.id),
            "number": s.number,
            "customer_name": s.customer_name or "CONSUMIDOR FINAL",
            "payment_method": pm,
            "total": float(s.total or 0),
            "fel_status": s.fel_status or "certified",
            "time": loc_dt.strftime("%H:%M") if loc_dt else "",
        })

    return items


@router.get("/sales-by-hour")
async def get_sales_by_hour(
    period: str = Query("month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener distribución de transacciones y ventas por hora (Horas Pico) en hora local de Guatemala."""
    p_start, p_end, _, _, _ = resolve_period_dates(period, start_date, end_date)

    stmt = select(
        SaleOrder.sale_date,
        SaleOrder.total
    ).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= p_start,
        SaleOrder.sale_date <= p_end,
        SaleOrder.state != SaleState.CANCELLED
    )
    if branch_id:
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    rows = res.all()

    hours_sales = {h: 0.0 for h in range(0, 24)}
    hours_count = {h: 0 for h in range(0, 24)}

    has_early_sales = False
    for s_date, total in rows:
        loc_dt = to_local_dt(s_date)
        if loc_dt:
            h = loc_dt.hour
            hours_sales[h] += float(total or 0)
            hours_count[h] += 1
            if h < 6:
                has_early_sales = True

    # Rango de horas: de 06:00 a 23:00 (o 00:00 a 23:00 si hubo ventas en la madrugada)
    display_range = range(0, 24) if has_early_sales else range(6, 24)
    labels = [f"{h:02d}:00" for h in display_range]
    sales_values = [round(hours_sales[h], 2) for h in display_range]
    counts_values = [hours_count[h] for h in display_range]

    return {
        "labels": labels,
        "sales": sales_values,
        "counts": counts_values,
        "period": period
    }


@router.get("/stock-alerts")
async def get_stock_alerts(
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener lista de productos con inventario en nivel crítico o stock bajo."""
    stmt = (
        select(Product)
        .options(selectinload(Product.category))
        .where(
            Product.company_id == current_user.company_id,
            Product.is_active == True,
            Product.min_stock > 0
        )
    )
    res = await db.execute(stmt)
    products = res.scalars().all()

    alerts = []
    for p in products:
        sq_query = select(func.coalesce(func.sum(StockQuant.qty_on_hand), Decimal("0"))).where(StockQuant.product_id == p.id)
        if branch_id:
            try:
                b_uuid = uuid.UUID(branch_id)
                sq_query = sq_query.join(Warehouse, StockQuant.warehouse_id == Warehouse.id).where(Warehouse.branch_id == b_uuid)
            except ValueError:
                pass
        sq_res = await db.execute(sq_query)
        current_stock = float(sq_res.scalar() or 0)
        min_stock = float(p.min_stock or 0)
        if current_stock <= min_stock:
            deficit = max(0, min_stock - current_stock)
            status_level = "critical" if current_stock <= 0 else "warning"
            alerts.append({
                "product_id": str(p.id),
                "name": p.name,
                "barcode": p.barcode or p.internal_ref or "",
                "category": p.category.name if p.category else "General",
                "current_stock": current_stock,
                "min_stock": min_stock,
                "deficit": deficit,
                "status": status_level,
                "sale_price": float(p.sale_price or 0)
            })

    alerts.sort(key=lambda x: (x["status"] != "critical", -x["deficit"]))
    return {
        "total_alerts": len(alerts),
        "critical_count": sum(1 for a in alerts if a["status"] == "critical"),
        "warning_count": sum(1 for a in alerts if a["status"] == "warning"),
        "items": alerts[:20]
    }


@router.get("/profitability-and-runway")
async def get_profitability_and_runway(
    period: str = Query("month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener métricas de rentabilidad por producto, runway de inventario y clientes VIP."""
    p_start, p_end, _, _, _ = resolve_period_dates(period, start_date, end_date)
    
    # 1. Rentabilidad por producto
    line_stmt = (
        select(
            SaleOrderLine.product_name,
            SaleOrderLine.product_id,
            func.sum(SaleOrderLine.qty).label("total_qty"),
            func.sum(SaleOrderLine.subtotal).label("total_revenue"),
            func.sum(SaleOrderLine.qty * SaleOrderLine.cost_price).label("total_cogs")
        )
        .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state != SaleState.CANCELLED,
            SaleOrder.sale_date >= p_start,
            SaleOrder.sale_date <= p_end
        )
        .group_by(SaleOrderLine.product_name, SaleOrderLine.product_id)
    )
    if branch_id:
        try:
            line_stmt = line_stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass
    line_res = await db.execute(line_stmt)
    profitability_items = []
    sold_qty_map = {}

    for name, p_id, qty, revenue, cogs in line_res.all():
        rev = float(revenue or 0)
        cost = float(cogs or 0)
        q = float(qty or 0)
        gross_profit = rev - cost
        margin_pct = round((gross_profit / rev) * 100, 1) if rev > 0 else 0.0
        profitability_items.append({
            "product_name": name,
            "product_id": str(p_id) if p_id else None,
            "qty_sold": q,
            "revenue": round(rev, 2),
            "cost": round(cost, 2),
            "gross_profit": round(gross_profit, 2),
            "margin_pct": margin_pct
        })
        if p_id:
            sold_qty_map[str(p_id)] = q

    profitability_items.sort(key=lambda x: x["gross_profit"], reverse=True)

    # 2. Runway de stock
    prod_stmt = select(Product).options(selectinload(Product.category)).where(
        Product.company_id == current_user.company_id,
        Product.is_active == True
    )
    prod_res = await db.execute(prod_stmt)
    products = prod_res.scalars().all()

    runway_items = []
    for p in products:
        p_id_str = str(p.id)
        sq_query = select(func.coalesce(func.sum(StockQuant.qty_on_hand), Decimal("0"))).where(StockQuant.product_id == p.id)
        if branch_id:
            try:
                b_uuid = uuid.UUID(branch_id)
                sq_query = sq_query.join(Warehouse, StockQuant.warehouse_id == Warehouse.id).where(Warehouse.branch_id == b_uuid)
            except ValueError:
                pass
        sq_res = await db.execute(sq_query)
        current_stock = float(sq_res.scalar() or 0)
        qty_sold_30d = sold_qty_map.get(p_id_str, 0.0)
        daily_velocity = qty_sold_30d / 30.0

        if daily_velocity > 0:
            days_left = round(current_stock / daily_velocity, 1)
        else:
            days_left = 999.0 if current_stock > 0 else 0.0

        if current_stock > 0 and days_left <= 30:
            runway_items.append({
                "product_id": p_id_str,
                "name": p.name,
                "current_stock": current_stock,
                "daily_velocity": round(daily_velocity, 2),
                "days_left": days_left,
                "urgency": "critical" if days_left <= 7 else "moderate"
            })

    runway_items.sort(key=lambda x: x["days_left"])

    # 3. Clientes VIP
    vip_stmt = (
        select(
            SaleOrder.customer_name,
            SaleOrder.customer_nit,
            func.count(SaleOrder.id).label("orders_count"),
            func.sum(SaleOrder.total).label("total_spent"),
            func.max(SaleOrder.sale_date).label("last_purchase")
        )
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state != SaleState.CANCELLED,
            SaleOrder.customer_nit != "CF"
        )
        .group_by(SaleOrder.customer_name, SaleOrder.customer_nit)
        .order_by(func.sum(SaleOrder.total).desc())
        .limit(10)
    )
    if branch_id:
        try:
            vip_stmt = vip_stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass
    vip_res = await db.execute(vip_stmt)
    vip_customers = [
        {
            "name": row[0],
            "nit": row[1],
            "orders_count": int(row[2]),
            "total_spent": round(float(row[3] or 0), 2),
            "last_purchase": row[4].strftime("%d/%m/%Y") if row[4] else None
        }
        for row in vip_res.all()
    ]

    return {
        "top_profitable_products": profitability_items[:10],
        "stock_runway_alerts": runway_items[:15],
        "vip_customers": vip_customers
    }
