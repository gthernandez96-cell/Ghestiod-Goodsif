"""API v1 — Reportes Financieros, Comerciales y BI 360°."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date, desc, or_
from sqlalchemy.orm import selectinload, joinedload
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from typing import Optional, List
import uuid
import io
import csv

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SalePayment, SaleState, SaleType
from app.models.fel import FELDocument
from app.models.product import Product, ProductCategory
from app.models.company import Warehouse, Branch
from app.models.partner import Partner
from app.models.inventory import StockQuant
from app.models.user import User
from app.models.ar import CustomerInvoiceCredit
from app.models.cashier import CashierSession, SessionState

router = APIRouter(prefix="/reports")

try:
    from zoneinfo import ZoneInfo
    GUATEMALA_TZ = ZoneInfo("America/Guatemala")
except Exception:
    GUATEMALA_TZ = timezone(timedelta(hours=-6))


def to_local_dt(dt: Optional[datetime], tz=GUATEMALA_TZ) -> Optional[datetime]:
    """Convierte un datetime (almacenado en UTC en la BD) a la zona horaria local de Guatemala (UTC-6)."""
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


def _parse_dates(start_date: Optional[str], end_date: Optional[str]):
    now_loc = datetime.now(GUATEMALA_TZ)
    today_loc = now_loc.date()

    if start_date:
        try:
            s_d = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
            s_dt_loc = datetime.combine(s_d, datetime.min.time(), tzinfo=GUATEMALA_TZ)
        except Exception:
            s_dt_loc = datetime.combine(today_loc - timedelta(days=30), datetime.min.time(), tzinfo=GUATEMALA_TZ)
    else:
        s_dt_loc = datetime.combine(today_loc - timedelta(days=30), datetime.min.time(), tzinfo=GUATEMALA_TZ)

    if end_date:
        try:
            e_d = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
            e_dt_loc = datetime.combine(e_d, datetime.max.time(), tzinfo=GUATEMALA_TZ)
        except Exception:
            e_dt_loc = datetime.combine(today_loc, datetime.max.time(), tzinfo=GUATEMALA_TZ)
    else:
        e_dt_loc = datetime.combine(today_loc, datetime.max.time(), tzinfo=GUATEMALA_TZ)

    s_dt = to_utc_naive(s_dt_loc)
    e_dt = to_utc_naive(e_dt_loc)
    return s_dt, e_dt


# ===== 1. EXECUTIVE KPIS BAR =====
@router.get("/executive-kpis")
async def get_executive_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Métricas ejecutivas de ventas, costos, rentabilidad y valoración."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    # Ventas en el período
    sales_stmt = select(
        func.count(SaleOrder.id).label("total_orders"),
        func.sum(SaleOrder.subtotal).label("total_subtotal"),
        func.sum(SaleOrder.tax_amount).label("total_tax"),
        func.sum(SaleOrder.total).label("total_sales")
    ).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_date >= s_dt,
        SaleOrder.sale_date <= e_dt,
        SaleOrder.state != SaleState.CANCELLED
    )
    sales_res = await db.execute(sales_stmt)
    s_row = sales_res.one()

    total_orders = int(s_row.total_orders or 0)
    total_sales = float(s_row.total_sales or 0.0)
    total_subtotal = float(s_row.total_subtotal or 0.0)
    total_tax = float(s_row.total_tax or 0.0)

    # Costo de Mercadería Vendida (COGS)
    cogs_stmt = (
        select(func.sum(SaleOrderLine.cost_price * SaleOrderLine.qty))
        .join(SaleOrder)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= s_dt,
            SaleOrder.sale_date <= e_dt,
            SaleOrder.state != SaleState.CANCELLED
        )
    )
    cogs_res = await db.execute(cogs_stmt)
    cogs = float(cogs_res.scalar() or 0.0)

    gross_profit = total_subtotal - cogs
    profit_margin_pct = (gross_profit / total_subtotal * 100.0) if total_subtotal > 0 else 0.0
    avg_ticket = (total_sales / total_orders) if total_orders > 0 else 0.0

    # Valoración actual de Inventario Físico
    inv_stmt = (
        select(func.sum(StockQuant.qty_on_hand * Product.cost_price))
        .join(Product, StockQuant.product_id == Product.id)
        .where(Product.company_id == current_user.company_id)
    )
    inv_res = await db.execute(inv_stmt)
    inventory_valuation = float(inv_res.scalar() or 0.0)

    return {
        "total_sales": total_sales,
        "total_subtotal": total_subtotal,
        "total_tax": total_tax,
        "total_orders": total_orders,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "profit_margin_pct": round(profit_margin_pct, 1),
        "avg_ticket": round(avg_ticket, 2),
        "inventory_valuation": inventory_valuation,
    }


# ===== 2. SALES BY PERIOD =====
@router.get("/sales-by-period")
async def sales_by_period(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Reporte de ventas y rentabilidad agrupado por fecha."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines))
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= s_dt,
            SaleOrder.sale_date <= e_dt,
            SaleOrder.state != SaleState.CANCELLED
        )
        .order_by(SaleOrder.sale_date)
    )
    res = await db.execute(stmt)
    orders = res.scalars().all()

    daily_map = {}
    for o in orders:
        loc_dt = to_local_dt(o.sale_date or o.created_at) if o else None
        d_str = loc_dt.strftime("%Y-%m-%d") if loc_dt else "N/A"
        if d_str not in daily_map:
            daily_map[d_str] = {
                "date": d_str,
                "transactions": 0,
                "subtotal": 0.0,
                "cost": 0.0,
                "profit": 0.0,
                "tax_amount": 0.0,
                "total": 0.0
            }
        daily_map[d_str]["transactions"] += 1
        daily_map[d_str]["subtotal"] += float(o.subtotal or 0)
        daily_map[d_str]["tax_amount"] += float(o.tax_amount or 0)
        daily_map[d_str]["total"] += float(o.total or 0)

        for line in o.lines:
            daily_map[d_str]["cost"] += float((line.cost_price or 0) * (line.qty or 0))

    items = []
    for d_str, data in sorted(daily_map.items()):
        sub = data["subtotal"]
        c = data["cost"]
        profit = sub - c
        margin = (profit / sub * 100.0) if sub > 0 else 0.0
        data["profit"] = profit
        data["margin_pct"] = round(margin, 1)
        items.append(data)

    return items


# ===== 3. TOP PRODUCTS (PARETO / ABC) =====
@router.get("/top-products")
async def top_products_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ranking de productos más vendidos y rentables."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(
            SaleOrderLine.product_id,
            SaleOrderLine.product_name,
            func.sum(SaleOrderLine.qty).label("units_sold"),
            func.sum(SaleOrderLine.total).label("total_revenue"),
            func.sum(SaleOrderLine.cost_price * SaleOrderLine.qty).label("total_cost")
        )
        .join(SaleOrder)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= s_dt,
            SaleOrder.sale_date <= e_dt,
            SaleOrder.state != SaleState.CANCELLED
        )
        .group_by(SaleOrderLine.product_id, SaleOrderLine.product_name)
        .order_by(desc(func.sum(SaleOrderLine.total)))
        .limit(limit)
    )

    res = await db.execute(stmt)
    rows = res.all()

    items = []
    for r in rows:
        rev = float(r.total_revenue or 0)
        cost = float(r.total_cost or 0)
        profit = rev - cost
        margin = (profit / rev * 100.0) if rev > 0 else 0.0

        items.append({
            "product_id": str(r.product_id),
            "product_name": r.product_name,
            "units_sold": float(r.units_sold or 0),
            "total_revenue": rev,
            "total_cost": cost,
            "gross_profit": profit,
            "margin_pct": round(margin, 1)
        })

    return items


# ===== 4. INVENTORY VALUATION BY WAREHOUSE =====
@router.get("/inventory-valuation")
async def inventory_valuation_report(
    warehouse_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Valoración de inventario desglosada por almacén y categoría."""
    stmt = (
        select(
            Warehouse.name.label("warehouse_name"),
            Warehouse.code.label("warehouse_code"),
            Product.id.label("product_id"),
            Product.name.label("product_name"),
            Product.internal_ref.label("internal_ref"),
            Product.cost_price.label("cost_price"),
            Product.sale_price.label("sale_price"),
            StockQuant.qty_on_hand.label("stock")
        )
        .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
        .join(Branch, Warehouse.branch_id == Branch.id)
        .join(Product, StockQuant.product_id == Product.id)
        .where(
            Branch.company_id == current_user.company_id,
            StockQuant.qty_on_hand > 0
        )
        .order_by(Warehouse.name, Product.name)
    )

    if warehouse_id:
        try:
            stmt = stmt.where(Warehouse.id == uuid.UUID(warehouse_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    rows = res.all()

    items = []
    for r in rows:
        qty = float(r.stock or 0)
        cost = float(r.cost_price or 0)
        sale = float(r.sale_price or 0)
        val_cost = qty * cost
        val_sale = qty * sale

        items.append({
            "warehouse_name": r.warehouse_name,
            "warehouse_code": r.warehouse_code,
            "product_id": str(r.product_id),
            "product_name": r.product_name,
            "internal_ref": r.internal_ref or "",
            "stock": qty,
            "cost_price": cost,
            "sale_price": sale,
            "valuation_cost": val_cost,
            "valuation_sale": val_sale,
            "expected_margin": val_sale - val_cost
        })

    return items


# ===== 5. PAYMENT METHODS BREAKDOWN =====
@router.get("/payment-methods")
async def payment_methods_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Distribución de ingresos por método de pago."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(
            SalePayment.payment_method,
            func.count(SalePayment.id).label("count"),
            func.sum(SalePayment.amount).label("total")
        )
        .join(SaleOrder)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_date >= s_dt,
            SaleOrder.sale_date <= e_dt,
            SaleOrder.state != SaleState.CANCELLED
        )
        .group_by(SalePayment.payment_method)
    )

    res = await db.execute(stmt)
    rows = res.all()

    total_amount = sum(float(r.total or 0) for r in rows)

    items = []
    for r in rows:
        amt = float(r.total or 0)
        pct = (amt / total_amount * 100.0) if total_amount > 0 else 0.0
        method_str = r.payment_method.value if hasattr(r.payment_method, 'value') else str(r.payment_method)

        items.append({
            "payment_method": method_str,
            "transaction_count": int(r.count or 0),
            "amount": amt,
            "percentage": round(pct, 1)
        })

    return items


# ===== 6. FEL SALES LEDGER (ASISTELIBROS SAT) =====
@router.get("/fel-ledger")
async def fel_ledger_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Libro oficial de ventas FEL conforme a los requerimientos de la SAT."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(FELDocument)
        .where(
            FELDocument.certified_at >= s_dt,
            FELDocument.certified_at <= e_dt
        )
        .order_by(FELDocument.certified_at.desc())
    )
    res = await db.execute(stmt)
    docs = res.scalars().all()

    return [
        {
            "fecha": to_local_dt(d.certified_at or d.created_at).strftime("%d/%m/%Y %H:%M") if (d and (d.certified_at or d.created_at)) else "",
            "tipo_dte": d.dte_type or "FACT",
            "serie": d.series or "FE",
            "numero": d.number or "",
            "uuid": d.uuid or "",
            "nit_receptor": d.receiver_nit or "CF",
            "nombre_receptor": d.receiver_name or "Consumidor Final",
            "subtotal": round(float((d.total or 0) / Decimal("1.12")), 2),
            "iva": round(float((d.total or 0) - ((d.total or 0) / Decimal("1.12"))), 2),
            "total": float(d.total or 0),
            "estado": d.status.value if hasattr(d.status, 'value') else str(d.status),
        }
        for d in docs
    ]


@router.get("/fel-ledger-csv")
async def export_fel_ledger_csv(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar Libro de Ventas FEL / SAT AsisteLibros a CSV."""
    import csv
    import io
    from fastapi.responses import Response

    s_dt, e_dt = _parse_dates(start_date, end_date)
    stmt = (
        select(FELDocument)
        .where(
            FELDocument.certified_at >= s_dt,
            FELDocument.certified_at <= e_dt
        )
        .order_by(FELDocument.certified_at.desc())
    )
    res = await db.execute(stmt)
    docs = res.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "Fecha Certificación", "Tipo DTE", "Serie", "Número DTE",
        "Autorización (CAE / UUID)", "NIT Receptor", "Nombre Receptor",
        "Subtotal (Q)", "IVA 12% (Q)", "Total (Q)", "Estado"
    ])

    for d in docs:
        tot = Decimal(str(d.total or 0))
        sub = round(tot / Decimal("1.12"), 2)
        iva = tot - sub
        loc_cert = to_local_dt(d.certified_at or d.created_at) if d else None
        writer.writerow([
            loc_cert.strftime("%d/%m/%Y %H:%M:%S") if loc_cert else "",
            d.dte_type or "FACT",
            d.series or "FE",
            d.number or "",
            d.uuid or "",
            d.receiver_nit or "CF",
            d.receiver_name or "Consumidor Final",
            f"{sub:.2f}",
            f"{iva:.2f}",
            f"{tot:.2f}",
            d.status.value if hasattr(d.status, 'value') else str(d.status)
        ])

    csv_data = "\ufeff" + output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="libro_ventas_fel_sat.csv"'}
    )


# ===== 8. DETALLE DE VENTAS (AUDITORÍA LÍNEA POR LÍNEA) =====
async def _get_sales_detail_data(
    db: AsyncSession,
    company_id: uuid.UUID,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    branch_id: Optional[str] = None,
    payment_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
):
    """Consulta consolidada de líneas de venta para reporte y exportación."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(SaleOrderLine)
        .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
        .outerjoin(Product, SaleOrderLine.product_id == Product.id)
        .outerjoin(ProductCategory, Product.category_id == ProductCategory.id)
        .outerjoin(Branch, SaleOrder.branch_id == Branch.id)
        .outerjoin(Partner, SaleOrder.customer_id == Partner.id)
        .options(
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.branch),
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.customer),
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.payments),
            selectinload(SaleOrderLine.product).selectinload(Product.category),
        )
        .where(
            SaleOrder.company_id == company_id,
            SaleOrder.sale_date >= s_dt,
            SaleOrder.sale_date <= e_dt,
            SaleOrder.state != SaleState.CANCELLED,
        )
        .order_by(SaleOrder.sale_date.desc(), SaleOrder.id.desc(), SaleOrderLine.id.asc())
    )

    if branch_id and branch_id.strip() and branch_id.strip() != "all":
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id.strip()))
        except ValueError:
            pass

    if payment_type and payment_type.strip() and payment_type.strip() != "all":
        pt = payment_type.strip().lower()
        if pt == "cash":
            stmt = stmt.where(SaleOrder.sale_type != SaleType.CREDIT, SaleOrder.amount_due <= 0)
        elif pt == "credit":
            stmt = stmt.where(or_(SaleOrder.sale_type == SaleType.CREDIT, SaleOrder.amount_due > 0))

    if search and search.strip():
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                SaleOrderLine.product_name.ilike(term),
                SaleOrderLine.product_barcode.ilike(term),
                Product.name.ilike(term),
                Product.internal_ref.ilike(term),
                Product.barcode.ilike(term),
                SaleOrder.number.ilike(term),
                SaleOrder.fel_number.ilike(term),
                SaleOrder.customer_name.ilike(term),
                SaleOrder.customer_nit.ilike(term),
            )
        )

    if offset:
        stmt = stmt.offset(offset)
    if limit:
        stmt = stmt.limit(limit)

    res = await db.execute(stmt)
    lines = res.scalars().all()

    items = []
    total_qty = 0.0
    total_sales = 0.0
    total_cost = 0.0
    total_surplus = 0.0
    total_cash = 0.0
    total_credit = 0.0

    for line in lines:
        sale = line.sale
        prod = line.product

        qty_val = float(line.qty or 0)
        costo_val = float(line.cost_price or (prod.cost_price if prod else 0) or 0)

        tot_venta = round(float(line.total or 0), 2)
        if tot_venta <= 0 and line.unit_price:
            tot_venta = round(qty_val * float(line.unit_price or 0), 2)

        pventa_val = round(tot_venta / qty_val, 2) if qty_val > 0 else float(line.unit_price or 0)
        tot_costo = round(qty_val * costo_val, 2)
        excedente = round(tot_venta - tot_costo, 2)

        # Determinar desglose Contado vs Crédito
        sale_tot = float(sale.total or 0) if (sale and sale.total is not None) else 0.0
        if sale_tot <= 0:
            sale_tot = tot_venta

        # Montos de pagos registrados (safe access sin lazy load)
        payments = []
        if sale:
            try:
                payments = sale.payments or []
            except Exception:
                payments = []

        paid_credit = sum(float(p.amount or 0) for p in payments if getattr(p, "payment_method", "") == "credit")
        paid_contado = sum(float(p.amount or 0) for p in payments if getattr(p, "payment_method", "") != "credit")
        unpaid_due = max(0.0, float(sale.amount_due or 0)) if (sale and sale.amount_due is not None) else 0.0

        # Saldo pendiente no cubierto o pago registrado como crédito
        total_credit_amt = paid_credit
        if unpaid_due > 0.001:
            total_credit_amt += unpaid_due
        elif sale and sale.sale_type == SaleType.CREDIT and total_credit_amt <= 0:
            total_credit_amt = sale_tot

        total_contado_amt = paid_contado

        # Si no hay pagos registrados en la orden
        if not payments:
            if (sale and sale.sale_type == SaleType.CREDIT) or unpaid_due > 0.001:
                total_credit_amt = sale_tot
                total_contado_amt = 0.0
            else:
                total_contado_amt = sale_tot
                total_credit_amt = 0.0

        total_tracked = total_credit_amt + total_contado_amt
        if total_tracked > 0:
            cred_ratio = max(0.0, min(1.0, total_credit_amt / total_tracked))
        elif (sale and sale.sale_type == SaleType.CREDIT) or unpaid_due > 0.001:
            cred_ratio = 1.0
        else:
            cred_ratio = 0.0

        f_credito = round(tot_venta * cred_ratio, 2)
        f_contado = round(tot_venta - f_credito, 2)
        tipo_venta = "Crédito" if cred_ratio > 0.001 else "Contado"

        # Cliente CF o nombre (safe access)
        raw_name = (sale.customer_name or "") if sale else ""
        if not raw_name and sale:
            try:
                raw_name = sale.customer.name if sale.customer else ""
            except Exception:
                pass
        raw_name = (raw_name or "").strip()
        if not raw_name or raw_name.upper() in {"CF", "C/F", "CONSUMIDOR FINAL", "CLIENTE FINAL", "N/A"}:
            client_name = "CF"
        else:
            client_name = raw_name

        raw_id = (sale.customer_nit or "") if sale else ""
        if not raw_id and sale:
            try:
                raw_id = (sale.customer.tax_id or sale.customer.code or "") if sale.customer else ""
            except Exception:
                pass
        raw_id = (raw_id or "").strip()
        if not raw_id or raw_id.upper() in {"CF", "C/F", "CONSUMIDOR FINAL"}:
            client_id = "CF"
        else:
            client_id = raw_id

        loc_dt = to_local_dt(sale.sale_date or sale.created_at) if sale else None
        fecha_str = loc_dt.strftime("%d/%m/%Y %H:%M") if loc_dt else ""

        sucursal_str = "Sucursal Central"
        if sale:
            try:
                if sale.branch and sale.branch.name:
                    sucursal_str = sale.branch.name
            except Exception:
                pass

        categoria_str = "General"
        if prod:
            try:
                if prod.category and prod.category.name:
                    categoria_str = prod.category.name
            except Exception:
                pass

        barcode_str = line.product_barcode or (prod.barcode if prod else "") or ""
        articulo_str = line.product_name or (prod.name if prod else "N/A")
        ticket_str = (sale.fel_number or sale.number or "") if sale else ""


        total_qty += qty_val
        total_sales += tot_venta
        total_cost += tot_costo
        total_surplus += excedente
        total_cash += f_contado
        total_credit += f_credito

        items.append({
            "fecha_factura": fecha_str,
            "sucursal": sucursal_str,
            "categoria": categoria_str,
            "codigo_de_barras": barcode_str,
            "articulo": articulo_str,
            "no_ticket": ticket_str,
            "cantidad": qty_val,
            "pventa": pventa_val,
            "costo": costo_val,
            "total_venta": tot_venta,
            "excedente": excedente,
            "tipo_de_venta": tipo_venta,
            "f_contado": f_contado,
            "f_credito": f_credito,
            "id_cliente": client_id,
            "nombre_del_cliente": client_name,
        })

    summary = {
        "total_records": len(items),
        "total_qty": round(total_qty, 2),
        "total_sales": round(total_sales, 2),
        "total_cost": round(total_cost, 2),
        "total_surplus": round(total_surplus, 2),
        "total_cash": round(total_cash, 2),
        "total_credit": round(total_credit, 2),
        "margin_pct": round((total_surplus / total_sales * 100), 2) if total_sales > 0 else 0.0,
    }

    return items, summary


@router.get("/sales-detail")
async def get_sales_detail(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    payment_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Consulta paginada del reporte de Detalle de Ventas para visualización en pantalla."""
    try:
        items, summary = await _get_sales_detail_data(
            db=db,
            company_id=current_user.company_id,
            start_date=start_date,
            end_date=end_date,
            branch_id=branch_id,
            payment_type=payment_type,
            search=search,
            limit=limit,
            offset=offset,
        )
        return {
            "items": items,
            "summary": summary,
            "total": summary.get("total_records", 0)
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error en get_sales_detail: {e}", exc_info=True)
        return {
            "items": [],
            "summary": {
                "total_records": 0,
                "total_qty": 0.0,
                "total_sales": 0.0,
                "total_cost": 0.0,
                "total_surplus": 0.0,
                "total_cash": 0.0,
                "total_credit": 0.0,
                "margin_pct": 0.0
            },
            "total": 0
        }




@router.get("/export-sales-detail-excel")
async def export_sales_detail_excel(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    payment_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar Detalle de Ventas a Excel (.xlsx) nativo con formato profesional de auditoría."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    items, summary = await _get_sales_detail_data(
        db=db,
        company_id=current_user.company_id,
        start_date=start_date,
        end_date=end_date,
        branch_id=branch_id,
        payment_type=payment_type,
        search=search,
        limit=10000,
        offset=0,
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Detalle de Ventas"
    ws.views.sheetView[0].showGridLines = True

    # Estilos
    font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    fill_title = PatternFill(start_color="714B67", end_color="714B67", fill_type="solid")
    font_subtitle = Font(name="Calibri", size=10, italic=True, color="475569")
    fill_subtitle = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="53334C", end_color="53334C", fill_type="solid")

    font_data = Font(name="Calibri", size=10, color="1E293B")
    font_total = Font(name="Calibri", size=11, bold=True, color="0F172A")
    fill_total = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")

    border_thin = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0")
    )
    border_total = Border(
        top=Side(style="thin", color="94A3B8"),
        bottom=Side(style="double", color="0F172A")
    )

    # 1. Fila de Título
    ws.merge_cells("A1:P1")
    cell_t = ws["A1"]
    cell_t.value = "POS EMPRESARIAL — REPORTE DETALLE DE VENTAS POR ARTÍCULO"
    cell_t.font = font_title
    cell_t.fill = fill_title
    cell_t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # 2. Fila de Subtítulo / Metadatos
    ws.merge_cells("A2:P2")
    cell_s = ws["A2"]
    loc_now = to_local_dt(datetime.now(timezone.utc))
    now_str = loc_now.strftime("%d/%m/%Y %H:%M") if loc_now else datetime.now().strftime("%d/%m/%Y %H:%M")
    cell_s.value = f"Período: {start_date or 'Histórico'} al {end_date or 'Hoy'} | Generado: {now_str} | Registros: {len(items)}"
    cell_s.font = font_subtitle
    cell_s.fill = fill_subtitle
    cell_s.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    # 3. Encabezados de Columna (Fila 4)
    headers = [
        "Fecha Factura",
        "Sucursal",
        "Categoría",
        "Código de Barras",
        "Artículo",
        "No. Ticket",
        "Cantidad",
        "P. Venta (Q)",
        "Costo (Q)",
        "Total Venta (Q)",
        "Excedente (Q)",
        "Tipo de Venta",
        "F. Contado (Q)",
        "F. Crédito (Q)",
        "ID Cliente",
        "Nombre del Cliente"
    ]
    ws.row_dimensions[4].height = 26
    for col_num, h_text in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col_num, value=h_text)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 4. Filas de Datos
    start_row = 5
    for idx, it in enumerate(items):
        row_num = start_row + idx
        row_values = [
            it["fecha_factura"],
            it["sucursal"],
            it["categoria"],
            it["codigo_de_barras"],
            it["articulo"],
            it["no_ticket"],
            it["cantidad"],
            it["pventa"],
            it["costo"],
            it["total_venta"],
            it["excedente"],
            it["tipo_de_venta"],
            it["f_contado"],
            it["f_credito"],
            it["id_cliente"],
            it["nombre_del_cliente"],
        ]
        ws.row_dimensions[row_num].height = 20
        for col_num, val in enumerate(row_values, 1):
            cell = ws.cell(row=row_num, column=col_num, value=val)
            cell.font = font_data
            cell.border = border_thin

            if col_num == 7:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_num in [8, 9, 10, 11, 13, 14]:
                cell.number_format = '"Q"#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_num in [1, 4, 6, 12, 15]:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # 5. Fila de Totales Generales
    tot_row = start_row + len(items)
    ws.row_dimensions[tot_row].height = 24

    ws.merge_cells(start_row=tot_row, start_column=1, end_row=tot_row, end_column=6)
    cell_tot_lbl = ws.cell(row=tot_row, column=1, value="TOTALES GENERALES")
    cell_tot_lbl.font = font_total
    cell_tot_lbl.fill = fill_total
    cell_tot_lbl.alignment = Alignment(horizontal="right", vertical="center")

    total_cols = {
        7: summary["total_qty"],
        8: None,
        9: None,
        10: summary["total_sales"],
        11: summary["total_surplus"],
        12: None,
        13: summary["total_cash"],
        14: summary["total_credit"],
        15: None,
        16: None
    }

    for col_num in range(1, 17):
        c = ws.cell(row=tot_row, column=col_num)
        c.fill = fill_total
        c.border = border_total
        if col_num in total_cols and total_cols[col_num] is not None:
            c.value = total_cols[col_num]
            c.font = font_total
            if col_num == 7:
                c.number_format = "#,##0.00"
            else:
                c.number_format = '"Q"#,##0.00'
            c.alignment = Alignment(horizontal="right", vertical="center")

    # 6. Autoajustar ancho de columnas
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in [1, 2]:
                continue
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"detalle_de_ventas_{start_date or 'inicio'}_{end_date or 'hoy'}.xlsx"
    return Response(
        content=out.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/export-sales-detail-csv")
async def export_sales_detail_csv(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    payment_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar Detalle de Ventas a CSV con UTF-8 BOM para apertura nativa directa en Excel."""
    items, summary = await _get_sales_detail_data(
        db=db,
        company_id=current_user.company_id,
        start_date=start_date,
        end_date=end_date,
        branch_id=branch_id,
        payment_type=payment_type,
        search=search,
        limit=10000,
        offset=0,
    )

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "Fecha factura",
        "Sucursal",
        "categoria",
        "codigo de barras",
        "articulo",
        "No. ticket",
        "cantidad",
        "pventa",
        "costo",
        "total venta",
        "excedente",
        "tipo de venta",
        "f contado",
        "f credito",
        "id clieente",
        "nombre del cliente"
    ])

    for it in items:
        writer.writerow([
            it["fecha_factura"],
            it["sucursal"],
            it["categoria"],
            it["codigo_de_barras"],
            it["articulo"],
            it["no_ticket"],
            f"{it['cantidad']:.2f}",
            f"{it['pventa']:.2f}",
            f"{it['costo']:.2f}",
            f"{it['total_venta']:.2f}",
            f"{it['excedente']:.2f}",
            it["tipo_de_venta"],
            f"{it['f_contado']:.2f}",
            f"{it['f_credito']:.2f}",
            it["id_cliente"],
            it["nombre_del_cliente"],
        ])

    csv_data = "\ufeff" + output.getvalue()
    filename = f"detalle_de_ventas_{start_date or 'inicio'}_{end_date or 'hoy'}.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# =====================================================================
# 8 NUEVOS REPORTES EJECUTIVOS Y BUSINESS INTELLIGENCE
# =====================================================================

# 1. RENDIMIENTO POR VENDEDOR / CAJERO
@router.get("/sales-by-seller")
async def get_sales_by_seller(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    commission_rate: float = Query(3.0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Reporte de rendimiento y ventas por Vendedor / Cajero con comisiones y métricas detalladas."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(SaleOrder)
        .options(
            selectinload(SaleOrder.user),
            selectinload(SaleOrder.lines),
            selectinload(SaleOrder.branch),
        )
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state.in_([SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]),
            SaleOrder.sale_date.between(s_dt, e_dt),
        )
    )
    if branch_id and branch_id.strip() and branch_id.strip() != "all":
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id.strip()))
        except Exception:
            pass

    res = await db.execute(stmt)
    sales = res.scalars().all()

    sellers_map = {}
    grand_total_sales = Decimal("0")
    grand_total_cost = Decimal("0")
    grand_total_profit = Decimal("0")
    grand_total_orders = 0
    grand_total_items = Decimal("0")
    grand_total_discounts = Decimal("0")
    comm_pct_dec = Decimal(str(max(0.0, commission_rate))) / Decimal("100")

    for s in sales:
        u_id = str(s.user_id) if s.user_id else "unassigned"
        u_name = s.user.full_name if s.user and s.user.full_name else (s.user.username if s.user else "Sin Asignar")
        u_email = s.user.email if s.user and s.user.email else ""
        u_avatar = s.user.avatar_url if s.user and hasattr(s.user, "avatar_url") else ""

        if u_id not in sellers_map:
            sellers_map[u_id] = {
                "user_id": u_id,
                "user_name": u_name,
                "email": u_email,
                "avatar_url": u_avatar or "",
                "total_orders": 0,
                "total_items_sold": Decimal("0"),
                "total_sales": Decimal("0"),
                "total_subtotal": Decimal("0"),
                "total_tax": Decimal("0"),
                "total_cost": Decimal("0"),
                "total_discounts": Decimal("0"),
                "gross_profit": Decimal("0"),
                "profit_margin_pct": 0.0,
                "avg_ticket": Decimal("0"),
                "pct_of_total": 0.0,
                "commission_amount": Decimal("0"),
            }

        data = sellers_map[u_id]
        data["total_orders"] += 1
        tot = Decimal(str(s.total or 0))
        sub = Decimal(str(s.subtotal or 0))
        tax = Decimal(str(s.tax_amount or 0))
        disc = Decimal(str(s.discount_amount or 0))

        data["total_sales"] += tot
        data["total_subtotal"] += sub
        data["total_tax"] += tax
        data["total_discounts"] += disc
        grand_total_discounts += disc

        cost_order = Decimal("0")
        items_order = Decimal("0")
        for line in s.lines:
            c = Decimal(str(line.cost_price or 0))
            q = Decimal(str(line.qty or 0))
            cost_order += (c * q)
            items_order += q
            if line.discount_amount and not disc:
                data["total_discounts"] += Decimal(str(line.discount_amount))
                grand_total_discounts += Decimal(str(line.discount_amount))

        data["total_cost"] += cost_order
        data["total_items_sold"] += items_order

        grand_total_orders += 1
        grand_total_sales += tot
        grand_total_cost += cost_order
        grand_total_items += items_order

    sellers_list = list(sellers_map.values())
    grand_total_commissions = Decimal("0")

    for s in sellers_list:
        s["gross_profit"] = s["total_subtotal"] - s["total_cost"]
        grand_total_profit += s["gross_profit"]
        if s["total_subtotal"] > Decimal("0"):
            s["profit_margin_pct"] = round(float((s["gross_profit"] / s["total_subtotal"]) * Decimal("100")), 2)
        else:
            s["profit_margin_pct"] = 0.0
        if s["total_orders"] > 0:
            s["avg_ticket"] = round(float(s["total_sales"] / Decimal(str(s["total_orders"]))), 2)
        else:
            s["avg_ticket"] = 0.0
        if grand_total_sales > Decimal("0"):
            s["pct_of_total"] = round(float((s["total_sales"] / grand_total_sales) * Decimal("100")), 2)
        else:
            s["pct_of_total"] = 0.0

        comm = (s["total_sales"] * comm_pct_dec).quantize(Decimal("0.01"))
        s["commission_amount"] = float(comm)
        grand_total_commissions += comm

        s["total_sales"] = float(s["total_sales"])
        s["total_subtotal"] = float(s["total_subtotal"])
        s["total_tax"] = float(s["total_tax"])
        s["total_cost"] = float(s["total_cost"])
        s["total_discounts"] = float(s["total_discounts"])
        s["total_items_sold"] = float(s["total_items_sold"])
        s["gross_profit"] = float(s["gross_profit"])

    sellers_list.sort(key=lambda x: x["total_sales"], reverse=True)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "commission_rate": commission_rate,
        "summary": {
            "total_sellers": len(sellers_list),
            "grand_total_orders": grand_total_orders,
            "grand_total_items_sold": float(grand_total_items),
            "grand_total_sales": float(grand_total_sales),
            "grand_total_cost": float(grand_total_cost),
            "grand_total_profit": float(grand_total_profit),
            "grand_total_discounts": float(grand_total_discounts),
            "grand_total_commissions": float(grand_total_commissions),
            "overall_margin_pct": round(float((grand_total_profit / grand_total_sales * Decimal("100"))), 2) if grand_total_sales > 0 else 0.0,
            "avg_ticket": round(float(grand_total_sales / grand_total_orders), 2) if grand_total_orders > 0 else 0.0
        },
        "sellers": sellers_list
    }


@router.get("/sales-by-seller/{seller_id}/detail")
async def get_seller_detail(
    seller_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    commission_rate: float = Query(3.0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Detalle de ventas y desglose de productos de un vendedor específico."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    seller_user = None
    if seller_id != "unassigned":
        try:
            seller_res = await db.execute(select(User).where(User.id == uuid.UUID(seller_id)))
            seller_user = seller_res.scalar_one_or_none()
        except Exception:
            pass

    stmt = (
        select(SaleOrder)
        .options(
            selectinload(SaleOrder.lines),
            selectinload(SaleOrder.branch),
            selectinload(SaleOrder.payments),
        )
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state.in_([SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]),
            SaleOrder.sale_date.between(s_dt, e_dt),
        )
    )
    if seller_id == "unassigned":
        stmt = stmt.where(SaleOrder.user_id.is_(None))
    else:
        try:
            stmt = stmt.where(SaleOrder.user_id == uuid.UUID(seller_id))
        except Exception:
            return {"error": "Invalid seller ID"}

    if branch_id and branch_id.strip() and branch_id.strip() != "all":
        try:
            stmt = stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id.strip()))
        except Exception:
            pass

    stmt = stmt.order_by(desc(SaleOrder.sale_date))
    res = await db.execute(stmt)
    orders = res.scalars().all()

    total_orders = len(orders)
    total_sales = Decimal("0")
    total_cost = Decimal("0")
    total_subtotal = Decimal("0")
    total_tax = Decimal("0")
    total_items = Decimal("0")
    total_discounts = Decimal("0")

    products_map = {}
    orders_list = []
    payments_map = {}

    for o in orders:
        tot = Decimal(str(o.total or 0))
        sub = Decimal(str(o.subtotal or 0))
        tax = Decimal(str(o.tax_amount or 0))
        disc = Decimal(str(o.discount_amount or 0))

        total_sales += tot
        total_subtotal += sub
        total_tax += tax
        total_discounts += disc

        order_cost = Decimal("0")
        order_items = Decimal("0")
        for line in o.lines:
            q = Decimal(str(line.qty or 0))
            c = Decimal(str(line.cost_price or 0))
            p_tot = Decimal(str(line.total or 0))
            order_cost += (c * q)
            order_items += q
            total_items += q

            p_id = str(line.product_id)
            if p_id not in products_map:
                products_map[p_id] = {
                    "product_id": p_id,
                    "product_name": line.product_name,
                    "barcode": line.product_barcode or "",
                    "qty_sold": Decimal("0"),
                    "total_sales": Decimal("0"),
                    "total_cost": Decimal("0"),
                    "gross_profit": Decimal("0"),
                    "profit_margin_pct": 0.0,
                }
            products_map[p_id]["qty_sold"] += q
            products_map[p_id]["total_sales"] += p_tot
            products_map[p_id]["total_cost"] += (c * q)

        total_cost += order_cost
        order_profit = sub - order_cost
        order_margin = (float(order_profit / sub) * 100.0) if sub > 0 else 0.0

        for pay in (o.payments or []):
            m = pay.payment_method or "other"
            payments_map[m] = payments_map.get(m, Decimal("0")) + Decimal(str(pay.amount or 0))

        loc_dt = to_local_dt(o.sale_date)
        orders_list.append({
            "id": str(o.id),
            "number": o.number,
            "date": loc_dt.strftime("%d/%m/%Y %H:%M") if loc_dt else "",
            "customer_name": o.customer_name or "Consumidor Final",
            "customer_nit": o.customer_nit or "CF",
            "branch_name": o.branch.name if o.branch else "",
            "items_count": float(order_items),
            "total": float(tot),
            "gross_profit": float(order_profit),
            "profit_margin_pct": round(order_margin, 1),
            "fel_number": o.fel_number or "",
            "state": o.state.value if hasattr(o.state, "value") else str(o.state),
        })

    top_products = list(products_map.values())
    for p in top_products:
        p["gross_profit"] = p["total_sales"] - p["total_cost"]
        p["profit_margin_pct"] = round(float(p["gross_profit"] / p["total_sales"] * 100), 1) if p["total_sales"] > 0 else 0.0
        p["qty_sold"] = float(p["qty_sold"])
        p["total_sales"] = float(p["total_sales"])
        p["total_cost"] = float(p["total_cost"])
        p["gross_profit"] = float(p["gross_profit"])

    top_products.sort(key=lambda x: x["total_sales"], reverse=True)

    gross_profit = total_subtotal - total_cost
    margin_pct = round(float((gross_profit / total_subtotal) * 100), 2) if total_subtotal > 0 else 0.0
    avg_ticket = round(float(total_sales / Decimal(str(total_orders))), 2) if total_orders > 0 else 0.0
    comm_pct_dec = Decimal(str(max(0.0, commission_rate))) / Decimal("100")
    comm_amount = round(float(total_sales * comm_pct_dec), 2)

    return {
        "seller": {
            "id": seller_id,
            "full_name": seller_user.full_name if seller_user else ("Sin Asignar" if seller_id == "unassigned" else "Desconocido"),
            "email": seller_user.email if seller_user else "",
            "username": seller_user.username if seller_user else "",
            "phone": getattr(seller_user, "phone", "") if seller_user else "",
            "is_active": getattr(seller_user, "is_active", True) if seller_user else True,
        },
        "period": {"start_date": start_date, "end_date": end_date},
        "summary": {
            "total_orders": total_orders,
            "total_items_sold": float(total_items),
            "total_sales": float(total_sales),
            "total_cost": float(total_cost),
            "gross_profit": float(gross_profit),
            "profit_margin_pct": margin_pct,
            "avg_ticket": avg_ticket,
            "total_discounts": float(total_discounts),
            "commission_rate": commission_rate,
            "commission_amount": comm_amount,
        },
        "payment_methods": {k: float(v) for k, v in payments_map.items()},
        "top_products": top_products[:15],
        "recent_orders": orders_list[:50],
    }


@router.get("/export-sales-by-seller-excel")
async def export_sales_by_seller_excel(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    commission_rate: float = Query(3.0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar Reporte de Rendimiento y Comisiones por Vendedor a Excel profesional (.xlsx)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    data = await get_sales_by_seller(
        start_date=start_date,
        end_date=end_date,
        branch_id=branch_id,
        commission_rate=commission_rate,
        db=db,
        current_user=current_user,
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ventas por Vendedor"
    ws.views.sheetView[0].showGridLines = True

    # Estilos
    font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    fill_title = PatternFill(start_color="714B67", end_color="714B67", fill_type="solid")
    font_subtitle = Font(name="Calibri", size=10, italic=True, color="475569")
    fill_subtitle = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="53334C", end_color="53334C", fill_type="solid")

    font_data = Font(name="Calibri", size=10, color="1E293B")
    font_total = Font(name="Calibri", size=11, bold=True, color="0F172A")
    fill_total = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")

    border_thin = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0")
    )
    border_total = Border(
        top=Side(style="thin", color="94A3B8"),
        bottom=Side(style="double", color="0F172A")
    )

    # 1. Título
    ws.merge_cells("A1:M1")
    cell_t = ws["A1"]
    cell_t.value = "POS EMPRESARIAL — REPORTE DE RENDIMIENTO Y COMISIONES POR VENDEDOR"
    cell_t.font = font_title
    cell_t.fill = fill_title
    cell_t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # 2. Metadatos
    ws.merge_cells("A2:M2")
    cell_sub = ws["A2"]
    now_str = datetime.now(GUATEMALA_TZ).strftime("%d/%m/%Y %H:%M:%S")
    cell_sub.value = f"Período: {start_date or 'Inicio'} al {end_date or 'Hoy'} | Tasa Comisión: {commission_rate}% | Generado: {now_str}"
    cell_sub.font = font_subtitle
    cell_sub.fill = fill_subtitle
    cell_sub.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    # 3. Encabezados de Tabla
    headers = [
        ("#", 6),
        ("Vendedor / Colaborador", 28),
        ("Correo / Usuario", 26),
        ("Órdenes", 12),
        ("Artículos", 12),
        ("Venta Total (Q)", 18),
        ("Descuentos (Q)", 16),
        ("Costo (Q)", 16),
        ("Ganancia Bruta (Q)", 18),
        ("Margen %", 12),
        ("Ticket Prom. (Q)", 16),
        ("% Aporte", 12),
        (f"Comisión ({commission_rate}%) (Q)", 20),
    ]

    header_row = 4
    ws.row_dimensions[header_row].height = 26
    for col_idx, (col_name, _) in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=col_name)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 4. Filas de Datos
    curr_row = 5
    for idx, s in enumerate(data.get("sellers", []), 1):
        ws.row_dimensions[curr_row].height = 20

        row_vals = [
            (idx, "center", None),
            (s.get("user_name"), "left", None),
            (s.get("email") or "-", "left", None),
            (s.get("total_orders", 0), "center", "#,##0"),
            (s.get("total_items_sold", 0), "center", "#,##0"),
            (s.get("total_sales", 0.0), "right", 'Q #,##0.00'),
            (s.get("total_discounts", 0.0), "right", 'Q #,##0.00'),
            (s.get("total_cost", 0.0), "right", 'Q #,##0.00'),
            (s.get("gross_profit", 0.0), "right", 'Q #,##0.00'),
            (s.get("profit_margin_pct", 0.0) / 100.0, "center", '0.0%'),
            (s.get("avg_ticket", 0.0), "right", 'Q #,##0.00'),
            (s.get("pct_of_total", 0.0) / 100.0, "center", '0.0%'),
            (s.get("commission_amount", 0.0), "right", 'Q #,##0.00'),
        ]

        for col_idx, (val, align, num_fmt) in enumerate(row_vals, 1):
            c = ws.cell(row=curr_row, column=col_idx, value=val)
            c.font = font_data
            c.border = border_thin
            c.alignment = Alignment(horizontal=align, vertical="center")
            if num_fmt:
                c.number_format = num_fmt

        curr_row += 1

    # 5. Fila de Totales
    ws.row_dimensions[curr_row].height = 24
    summary = data.get("summary", {})
    total_vals = [
        ("TOTAL", "center", None),
        (f"{summary.get('total_sellers', 0)} Vendedores", "left", None),
        ("", "left", None),
        (summary.get("grand_total_orders", 0), "center", "#,##0"),
        (summary.get("grand_total_items_sold", 0), "center", "#,##0"),
        (summary.get("grand_total_sales", 0.0), "right", 'Q #,##0.00'),
        (summary.get("grand_total_discounts", 0.0), "right", 'Q #,##0.00'),
        (summary.get("grand_total_cost", 0.0), "right", 'Q #,##0.00'),
        (summary.get("grand_total_profit", 0.0), "right", 'Q #,##0.00'),
        (summary.get("overall_margin_pct", 0.0) / 100.0, "center", '0.0%'),
        (summary.get("avg_ticket", 0.0), "right", 'Q #,##0.00'),
        (1.0 if summary.get("grand_total_sales", 0) > 0 else 0.0, "center", '0.0%'),
        (summary.get("grand_total_commissions", 0.0), "right", 'Q #,##0.00'),
    ]

    for col_idx, (val, align, num_fmt) in enumerate(total_vals, 1):
        c = ws.cell(row=curr_row, column=col_idx, value=val)
        c.font = font_total
        c.fill = fill_total
        c.border = border_total
        c.alignment = Alignment(horizontal=align, vertical="center")
        if num_fmt:
            c.number_format = num_fmt

    # Autoajustar columnas
    for col_idx, (_, width) in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = width

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"ventas_por_vendedor_{start_date or 'inicio'}_{end_date or 'hoy'}.xlsx"
    return Response(
        content=out.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/export-sales-by-seller-csv")
async def export_sales_by_seller_csv(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    commission_rate: float = Query(3.0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar Reporte de Rendimiento y Comisiones por Vendedor a CSV."""
    data = await get_sales_by_seller(
        start_date=start_date,
        end_date=end_date,
        branch_id=branch_id,
        commission_rate=commission_rate,
        db=db,
        current_user=current_user,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Ranking",
        "Vendedor",
        "Email",
        "Ordenes",
        "Articulos Vendidos",
        "Venta Total (Q)",
        "Descuentos (Q)",
        "Costo (Q)",
        "Ganancia Bruta (Q)",
        "Margen %",
        "Ticket Promedio (Q)",
        "% Aporte",
        f"Comision ({commission_rate}%) (Q)",
    ])

    for idx, s in enumerate(data.get("sellers", []), 1):
        writer.writerow([
            idx,
            s.get("user_name"),
            s.get("email"),
            s.get("total_orders"),
            s.get("total_items_sold"),
            f"{s.get('total_sales', 0.0):.2f}",
            f"{s.get('total_discounts', 0.0):.2f}",
            f"{s.get('total_cost', 0.0):.2f}",
            f"{s.get('gross_profit', 0.0):.2f}",
            f"{s.get('profit_margin_pct', 0.0):.2f}%",
            f"{s.get('avg_ticket', 0.0):.2f}",
            f"{s.get('pct_of_total', 0.0):.2f}%",
            f"{s.get('commission_amount', 0.0):.2f}",
        ])

    filename = f"ventas_por_vendedor_{start_date or 'inicio'}_{end_date or 'hoy'}.csv"
    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# 2. TOP CLIENTES & ANÁLISIS DE CARTERA
@router.get("/top-customers")
async def get_top_customers(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ranking Top Clientes, Frecuencia de Compra y Estado de Cartera."""
    s_dt, e_dt = _parse_dates(start_date, end_date)
    today_loc = datetime.now(GUATEMALA_TZ).date()

    stmt = (
        select(SaleOrder)
        .options(
            selectinload(SaleOrder.customer),
            selectinload(SaleOrder.lines),
        )
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state.in_([SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]),
            SaleOrder.sale_date.between(s_dt, e_dt),
        )
    )
    res = await db.execute(stmt)
    sales = res.scalars().all()

    cust_map = {}
    grand_total_sales = Decimal("0")

    for s in sales:
        c_id = str(s.customer_id) if s.customer_id else (f"nit_{s.customer_nit}" if s.customer_nit else "cf")
        c_name = (s.customer.name if s.customer else (s.customer_name or "Consumidor Final")).strip()
        c_nit = (s.customer.nit if s.customer and s.customer.nit else (s.customer_nit or "C/F")).strip()
        c_phone = (s.customer.phone or s.customer.mobile) if s.customer else ""
        c_balance = float(s.customer.balance or 0) if s.customer else 0.0

        if c_id not in cust_map:
            cust_map[c_id] = {
                "customer_id": c_id,
                "name": c_name,
                "nit": c_nit,
                "phone": c_phone,
                "credit_balance": c_balance,
                "total_orders": 0,
                "total_sales": Decimal("0"),
                "total_cost": Decimal("0"),
                "gross_profit": Decimal("0"),
                "last_sale_date": s.sale_date,
            }

        data = cust_map[c_id]
        data["total_orders"] += 1
        tot = Decimal(str(s.total or 0))
        data["total_sales"] += tot
        grand_total_sales += tot

        if s.sale_date and (data["last_sale_date"] is None or s.sale_date > data["last_sale_date"]):
            data["last_sale_date"] = s.sale_date

        for line in s.lines:
            c = Decimal(str(line.cost_price or 0))
            q = Decimal(str(line.qty or 0))
            data["total_cost"] += (c * q)

    cust_list = list(cust_map.values())
    for c in cust_list:
        c["gross_profit"] = c["total_sales"] - c["total_cost"]
        c["profit_margin_pct"] = round(float((c["gross_profit"] / c["total_sales"]) * Decimal("100")), 2) if c["total_sales"] > Decimal("0") else 0.0
        c["avg_ticket"] = round(float(c["total_sales"] / Decimal(str(c["total_orders"]))), 2) if c["total_orders"] > 0 else 0.0

        if c["last_sale_date"]:
            dt_loc = to_local_dt(c["last_sale_date"])
            days_inactive = (today_loc - dt_loc.date()).days
            c["last_sale_formatted"] = dt_loc.strftime("%d/%m/%Y")
            c["days_inactive"] = max(0, days_inactive)
            if days_inactive <= 15:
                c["activity_status"] = "Frecuente (Activo)"
                c["activity_color"] = "green"
            elif days_inactive <= 45:
                c["activity_status"] = "Regular"
                c["activity_color"] = "blue"
            else:
                c["activity_status"] = "En Riesgo / Inactivo"
                c["activity_color"] = "amber"
        else:
            c["last_sale_formatted"] = "-"
            c["days_inactive"] = 999
            c["activity_status"] = "Sin Registro"
            c["activity_color"] = "gray"

        c["pct_of_total"] = round(float((c["total_sales"] / grand_total_sales) * Decimal("100")), 2) if grand_total_sales > 0 else 0.0
        c["total_sales"] = float(c["total_sales"])
        c["total_cost"] = float(c["total_cost"])
        c["gross_profit"] = float(c["gross_profit"])

    cust_list.sort(key=lambda x: x["total_sales"], reverse=True)
    limited_list = cust_list[:limit]

    return {
        "start_date": start_date,
        "end_date": end_date,
        "summary": {
            "total_unique_customers": len(cust_list),
            "grand_total_sales": float(grand_total_sales),
            "active_customers": sum(1 for c in cust_list if c["activity_color"] == "green"),
            "at_risk_customers": sum(1 for c in cust_list if c["activity_color"] == "amber"),
        },
        "customers": limited_list
    }


# 3. HORAS PICO Y DÍAS DE MAYOR DEMANDA (HEATMAP)
@router.get("/sales-peak-hours")
async def get_sales_peak_hours(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Distribución de ventas por hora del día (UTC-6 Guatemala) y día de la semana."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(SaleOrder.sale_date, SaleOrder.total)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state.in_([SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]),
            SaleOrder.sale_date.between(s_dt, e_dt),
        )
    )
    res = await db.execute(stmt)
    rows = res.all()

    hourly = [{"hour": h, "label": f"{h:02d}:00 - {h:02d}:59", "orders": 0, "sales": 0.0} for h in range(24)]
    day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    daily = [{"day_idx": d, "day_name": day_names[d], "orders": 0, "sales": 0.0} for d in range(7)]

    grand_total_sales = 0.0
    grand_total_orders = len(rows)

    for sale_date, total in rows:
        tot = float(total or 0)
        grand_total_sales += tot
        loc_dt = to_local_dt(sale_date)
        if loc_dt:
            h = loc_dt.hour
            d = loc_dt.weekday()
            hourly[h]["orders"] += 1
            hourly[h]["sales"] += tot
            daily[d]["orders"] += 1
            daily[d]["sales"] += tot

    for it in hourly:
        it["sales"] = round(it["sales"], 2)
        it["avg_ticket"] = round(it["sales"] / it["orders"], 2) if it["orders"] > 0 else 0.0

    for it in daily:
        it["sales"] = round(it["sales"], 2)
        it["avg_ticket"] = round(it["sales"] / it["orders"], 2) if it["orders"] > 0 else 0.0

    max_hourly = max(hourly, key=lambda x: x["sales"], default=None)
    max_daily = max(daily, key=lambda x: x["sales"], default=None)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "summary": {
            "total_orders": grand_total_orders,
            "total_sales": round(grand_total_sales, 2),
            "peak_hour": max_hourly["label"] if max_hourly and max_hourly["sales"] > 0 else "N/A",
            "peak_hour_sales": max_hourly["sales"] if max_hourly else 0.0,
            "busiest_day": max_daily["day_name"] if max_daily and max_daily["sales"] > 0 else "N/A",
            "busiest_day_sales": max_daily["sales"] if max_daily else 0.0,
        },
        "hourly": hourly,
        "daily": daily,
    }


# 4. AUDITORÍA DE DESCUENTOS OTORGADOS
@router.get("/discounts-audit")
async def get_discounts_audit(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Auditoría detallada de descuentos y rebajas otorgadas."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(SaleOrderLine)
        .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
        .options(
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.user),
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.customer),
        )
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state.in_([SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]),
            SaleOrder.sale_date.between(s_dt, e_dt),
            or_(
                SaleOrderLine.discount_amount > 0,
                SaleOrderLine.discount_pct > 0,
                SaleOrder.discount_amount > 0
            )
        )
        .order_by(desc(SaleOrder.sale_date))
        .limit(limit)
    )
    res = await db.execute(stmt)
    lines = res.scalars().all()

    total_discount_amount = Decimal("0")
    total_sales_with_discount = Decimal("0")
    items = []

    for l in lines:
        s = l.sale
        disc_amt = Decimal(str(l.discount_amount or 0))
        if disc_amt == Decimal("0") and l.discount_pct and l.discount_pct > 0:
            disc_amt = (Decimal(str(l.unit_price or 0)) * Decimal(str(l.qty or 0))) * (Decimal(str(l.discount_pct)) / Decimal("100"))

        tot_line = Decimal(str(l.total or 0))
        cost_line = Decimal(str(l.cost_price or 0)) * Decimal(str(l.qty or 0))
        margin_line = tot_line - cost_line

        total_discount_amount += disc_amt
        total_sales_with_discount += tot_line

        dt_loc = to_local_dt(s.sale_date) if s.sale_date else None
        dt_str = dt_loc.strftime("%d/%m/%Y %I:%M %p") if dt_loc else "-"

        items.append({
            "sale_number": s.number,
            "sale_date": dt_str,
            "seller_name": s.user.full_name if s.user else (s.user.username if s.user else "N/A"),
            "customer_name": s.customer.name if s.customer else (s.customer_name or "Consumidor Final"),
            "product_name": l.product_name,
            "product_barcode": l.product_barcode or "-",
            "qty": float(l.qty or 0),
            "unit_price": float(l.unit_price or 0),
            "cost_price": float(l.cost_price or 0),
            "discount_pct": float(l.discount_pct or 0),
            "discount_amount": float(disc_amt),
            "total": float(tot_line),
            "margin_amount": float(margin_line),
        })

    return {
        "start_date": start_date,
        "end_date": end_date,
        "summary": {
            "total_records": len(items),
            "total_discount_amount": float(total_discount_amount),
            "total_sales_impacted": float(total_sales_with_discount),
            "avg_discount_per_record": round(float(total_discount_amount / Decimal(str(len(items)))), 2) if items else 0.0
        },
        "items": items
    }


# 5. PRODUCTOS DE LENTO MOVIMIENTO / STOCK ESTANCADO ("HUESOS")
@router.get("/slow-moving-stock")
async def get_slow_moving_stock(
    warehouse_id: Optional[str] = Query(None),
    days_threshold: int = Query(30, ge=7, le=365),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Productos con stock físico disponible pero con nula o muy baja venta (capital congelado)."""
    now_loc = datetime.now(GUATEMALA_TZ)
    cutoff_dt_loc = now_loc - timedelta(days=days_threshold)
    cutoff_dt_utc = to_utc_naive(cutoff_dt_loc)

    quant_stmt = (
        select(StockQuant)
        .join(Product, StockQuant.product_id == Product.id)
        .options(
            selectinload(StockQuant.product).selectinload(Product.category),
            selectinload(StockQuant.warehouse),
        )
        .where(
            Product.company_id == current_user.company_id,
            Product.is_active == True,
            StockQuant.qty_on_hand > 0,
        )
    )
    if warehouse_id:
        try:
            quant_stmt = quant_stmt.where(StockQuant.warehouse_id == uuid.UUID(warehouse_id))
        except Exception:
            pass

    res_quants = await db.execute(quant_stmt)
    quants = res_quants.scalars().all()

    sales_stmt = (
        select(
            SaleOrderLine.product_id,
            func.sum(SaleOrderLine.qty).label("qty_sold"),
            func.max(SaleOrder.sale_date).label("last_sold_date")
        )
        .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
        .where(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.state.in_([SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]),
            SaleOrder.sale_date >= cutoff_dt_utc,
        )
        .group_by(SaleOrderLine.product_id)
    )
    res_sales = await db.execute(sales_stmt)
    sales_by_prod = {row.product_id: (float(row.qty_sold or 0), row.last_sold_date) for row in res_sales.all()}

    slow_items = []
    total_locked_capital = Decimal("0")
    total_frozen_units = Decimal("0")

    for q in quants:
        p = q.product
        qty_on_hand = Decimal(str(q.qty_on_hand or 0))
        unit_cost = Decimal(str(p.cost_price or q.avg_cost or 0))
        capital_locked = qty_on_hand * unit_cost

        sold_info = sales_by_prod.get(p.id, (0.0, None))
        qty_sold = sold_info[0]
        last_sold = sold_info[1]

        if qty_sold == 0 or (float(qty_on_hand) > 0 and (qty_sold / float(qty_on_hand)) < 0.05):
            total_locked_capital += capital_locked
            total_frozen_units += qty_on_hand

            dt_loc = to_local_dt(last_sold) if last_sold else None
            last_sold_str = dt_loc.strftime("%d/%m/%Y") if dt_loc else "Sin ventas recientes"

            slow_items.append({
                "product_id": str(p.id),
                "sku": p.internal_ref or p.barcode or "-",
                "product_name": p.name,
                "category": p.category.name if p.category else "General",
                "warehouse_name": q.warehouse.name if q.warehouse else "Principal",
                "qty_on_hand": float(qty_on_hand),
                "qty_sold_in_period": qty_sold,
                "unit_cost": float(unit_cost),
                "sale_price": float(p.sale_price or 0),
                "capital_locked": float(capital_locked),
                "last_sold_date": last_sold_str,
                "days_threshold": days_threshold
            })

    slow_items.sort(key=lambda x: x["capital_locked"], reverse=True)
    limited_items = slow_items[:limit]

    return {
        "days_threshold": days_threshold,
        "summary": {
            "total_slow_products": len(slow_items),
            "total_frozen_units": float(total_frozen_units),
            "total_locked_capital": float(total_locked_capital),
        },
        "items": limited_items
    }


# 6. SUGERIDO DE COMPRAS Y ALERTAS DE STOCK MÍNIMO
@router.get("/purchasing-suggestions")
async def get_purchasing_suggestions(
    warehouse_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Alertas de quiebre de stock y cálculo automatizado de sugerido de reposición."""
    stmt = (
        select(StockQuant)
        .join(Product, StockQuant.product_id == Product.id)
        .options(
            selectinload(StockQuant.product).selectinload(Product.category),
            selectinload(StockQuant.product).selectinload(Product.supplier),
            selectinload(StockQuant.warehouse),
        )
        .where(
            Product.company_id == current_user.company_id,
            Product.is_active == True,
            Product.can_be_purchased == True,
            Product.min_stock > 0,
            StockQuant.qty_on_hand <= Product.min_stock,
        )
    )
    if warehouse_id:
        try:
            stmt = stmt.where(StockQuant.warehouse_id == uuid.UUID(warehouse_id))
        except Exception:
            pass

    res = await db.execute(stmt)
    quants = res.scalars().all()

    items = []
    critical_count = 0
    total_investment_needed = Decimal("0")

    for q in quants:
        p = q.product
        qty_on_hand = Decimal(str(q.qty_on_hand or 0))
        min_stock = Decimal(str(p.min_stock or 0))
        max_stock = Decimal(str(p.max_stock or 0))
        unit_cost = Decimal(str(p.cost_price or q.avg_cost or 0))

        if max_stock > min_stock:
            suggested_qty = max(Decimal("1"), max_stock - qty_on_hand)
        else:
            suggested_qty = max(Decimal("1"), (min_stock * Decimal("2")) - qty_on_hand)

        investment = suggested_qty * unit_cost
        total_investment_needed += investment

        is_critical = qty_on_hand <= Decimal("0")
        if is_critical:
            critical_count += 1

        items.append({
            "product_id": str(p.id),
            "sku": p.internal_ref or p.barcode or "-",
            "product_name": p.name,
            "category": p.category.name if p.category else "General",
            "warehouse_name": q.warehouse.name if q.warehouse else "Principal",
            "supplier_name": p.supplier.name if p.supplier else "No asignado",
            "qty_on_hand": float(qty_on_hand),
            "min_stock": float(min_stock),
            "max_stock": float(max_stock),
            "suggested_qty": float(suggested_qty),
            "unit_cost": float(unit_cost),
            "investment_needed": float(investment),
            "status": "CRÍTICO (Agotado)" if is_critical else "ALERTA (Bajo Mínimo)",
            "status_color": "red" if is_critical else "amber",
        })

    items.sort(key=lambda x: (0 if x["status_color"] == "red" else 1, -x["investment_needed"]))

    return {
        "summary": {
            "total_alerts": len(items),
            "critical_stockout": critical_count,
            "warning_low_stock": len(items) - critical_count,
            "total_investment_needed": float(total_investment_needed),
        },
        "items": items
    }


# 7. ANTIGÜEDAD DE SALDOS (AGING CUENTAS POR COBRAR AR)
@router.get("/ar-aging")
async def get_ar_aging(
    customer_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Antigüedad de saldos (Aging Report) de Cuentas por Cobrar."""
    today_loc = datetime.now(GUATEMALA_TZ).date()

    stmt = (
        select(CustomerInvoiceCredit)
        .options(
            selectinload(CustomerInvoiceCredit.customer),
            selectinload(CustomerInvoiceCredit.sale),
        )
        .where(
            CustomerInvoiceCredit.company_id == current_user.company_id,
            CustomerInvoiceCredit.balance > 0,
            CustomerInvoiceCredit.state.in_(["open", "overdue"]),
        )
    )
    if customer_id:
        try:
            stmt = stmt.where(CustomerInvoiceCredit.customer_id == uuid.UUID(customer_id))
        except Exception:
            pass

    res = await db.execute(stmt)
    invoices = res.scalars().all()

    cust_map = {}
    grand_total_portfolio = Decimal("0")
    grand_total_current = Decimal("0")
    grand_total_1_30 = Decimal("0")
    grand_total_31_60 = Decimal("0")
    grand_total_61_90 = Decimal("0")
    grand_total_over_90 = Decimal("0")

    for inv in invoices:
        cust = inv.customer
        c_id = str(inv.customer_id) if inv.customer_id else "unknown"
        c_name = cust.name if cust else "Cliente General"
        c_nit = cust.nit if cust else "-"
        c_phone = (cust.phone or cust.mobile) if cust else "-"
        c_limit = float(cust.credit_limit or 0) if cust else 0.0

        if c_id not in cust_map:
            cust_map[c_id] = {
                "customer_id": c_id,
                "customer_name": c_name,
                "nit": c_nit,
                "phone": c_phone,
                "credit_limit": c_limit,
                "total_balance": Decimal("0"),
                "current": Decimal("0"),
                "days_1_30": Decimal("0"),
                "days_31_60": Decimal("0"),
                "days_61_90": Decimal("0"),
                "days_over_90": Decimal("0"),
                "invoices_count": 0,
            }

        data = cust_map[c_id]
        bal = Decimal(str(inv.balance or 0))
        data["total_balance"] += bal
        data["invoices_count"] += 1
        grand_total_portfolio += bal

        due_d = inv.due_date.date() if inv.due_date else None
        if due_d is None or due_d >= today_loc:
            data["current"] += bal
            grand_total_current += bal
        else:
            days_over = (today_loc - due_d).days
            if days_over <= 30:
                data["days_1_30"] += bal
                grand_total_1_30 += bal
            elif days_over <= 60:
                data["days_31_60"] += bal
                grand_total_31_60 += bal
            elif days_over <= 90:
                data["days_61_90"] += bal
                grand_total_61_90 += bal
            else:
                data["days_over_90"] += bal
                grand_total_over_90 += bal

    cust_list = list(cust_map.values())
    for c in cust_list:
        c["total_balance"] = float(c["total_balance"])
        c["current"] = float(c["current"])
        c["days_1_30"] = float(c["days_1_30"])
        c["days_31_60"] = float(c["days_31_60"])
        c["days_61_90"] = float(c["days_61_90"])
        c["days_over_90"] = float(c["days_over_90"])
        c["total_overdue"] = c["days_1_30"] + c["days_31_60"] + c["days_61_90"] + c["days_over_90"]
        c["risk_level"] = "CRÍTICO" if c["days_over_90"] > 0 else ("ALTO" if c["days_61_90"] > 0 else ("MODERADO" if c["days_31_60"] > 0 else ("BAJO" if c["days_1_30"] > 0 else "AL DÍA")))
        c["risk_color"] = "red" if c["risk_level"] in ["CRÍTICO", "ALTO"] else ("amber" if c["risk_level"] in ["MODERADO", "BAJO"] else "green")

    cust_list.sort(key=lambda x: x["total_balance"], reverse=True)
    total_overdue_portfolio = grand_total_1_30 + grand_total_31_60 + grand_total_61_90 + grand_total_over_90

    return {
        "summary": {
            "total_customers_with_debt": len(cust_list),
            "grand_total_portfolio": float(grand_total_portfolio),
            "grand_total_current": float(grand_total_current),
            "grand_total_overdue": float(total_overdue_portfolio),
            "overdue_percentage": round(float((total_overdue_portfolio / grand_total_portfolio) * Decimal("100")), 2) if grand_total_portfolio > 0 else 0.0,
            "aging_brackets": {
                "current": float(grand_total_current),
                "days_1_30": float(grand_total_1_30),
                "days_31_60": float(grand_total_31_60),
                "days_61_90": float(grand_total_61_90),
                "days_over_90": float(grand_total_over_90),
            }
        },
        "customers": cust_list
    }


# 8. AUDITORÍA DE SESIONES Y CIERRES DE CAJA
@router.get("/cashier-audits")
async def get_cashier_audits(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Auditoría de arqueos y sesiones de caja con diferencias (sobrantes y faltantes)."""
    s_dt, e_dt = _parse_dates(start_date, end_date)

    stmt = (
        select(CashierSession)
        .join(User, CashierSession.user_id == User.id)
        .options(
            selectinload(CashierSession.user),
            selectinload(CashierSession.branch),
        )
        .where(
            User.company_id == current_user.company_id,
            CashierSession.created_at.between(s_dt, e_dt),
        )
        .order_by(desc(CashierSession.created_at))
        .limit(limit)
    )
    res = await db.execute(stmt)
    sessions = res.scalars().all()

    items = []
    total_diff = Decimal("0")
    faltantes_count = 0
    sobrantes_count = 0
    cuadrados_count = 0

    for s in sessions:
        diff = Decimal(str(s.closing_difference or 0))
        total_diff += diff

        if diff < Decimal("-0.01"):
            status_diff = "Faltante"
            status_color = "red"
            faltantes_count += 1
        elif diff > Decimal("0.01"):
            status_diff = "Sobrante"
            status_color = "amber"
            sobrantes_count += 1
        else:
            status_diff = "Exacto / Cuadrado"
            status_color = "green"
            cuadrados_count += 1

        open_loc = to_local_dt(s.opened_at) if s.opened_at else None
        close_loc = to_local_dt(s.closed_at) if s.closed_at else None

        items.append({
            "session_id": str(s.id),
            "session_number": s.session_number,
            "cajero_name": s.user.full_name if s.user else "N/A",
            "branch_name": s.branch.name if s.branch else "Principal",
            "state": s.state.value if hasattr(s.state, "value") else str(s.state),
            "opened_at": open_loc.strftime("%d/%m/%Y %I:%M %p") if open_loc else "-",
            "closed_at": close_loc.strftime("%d/%m/%Y %I:%M %p") if close_loc else ("En curso" if s.state == SessionState.OPEN else "-"),
            "opening_balance": float(s.opening_balance or 0),
            "total_sales": float(s.total_sales or 0),
            "closing_balance_expected": float(s.closing_balance_expected or 0),
            "closing_balance_real": float(s.closing_balance_real or 0),
            "closing_difference": float(diff),
            "status_diff": status_diff,
            "status_color": status_color,
        })

    return {
        "start_date": start_date,
        "end_date": end_date,
        "summary": {
            "total_sessions": len(items),
            "exact_sessions": cuadrados_count,
            "faltantes_sessions": faltantes_count,
            "sobrantes_sessions": sobrantes_count,
            "net_difference": float(total_diff),
        },
        "sessions": items
    }



