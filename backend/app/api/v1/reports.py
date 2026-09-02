"""API v1 — Reportes Financieros, Comerciales y BI 360°."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date, desc
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from typing import Optional, List
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SalePayment, SaleState
from app.models.fel import FELDocument
from app.models.product import Product, ProductCategory
from app.models.company import Warehouse, Branch
from app.models.inventory import StockQuant

router = APIRouter(prefix="/reports")


def _parse_dates(start_date: Optional[str], end_date: Optional[str]):
    now = datetime.now(timezone.utc)
    if start_date:
        try:
            s_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
        except Exception:
            s_dt = now - timedelta(days=30)
    else:
        s_dt = now - timedelta(days=30)

    if end_date:
        try:
            e_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        except Exception:
            e_dt = now
    else:
        e_dt = now

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
        d_str = o.sale_date.strftime("%Y-%m-%d") if o.sale_date else "N/A"
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
            "fecha": d.certified_at.strftime("%d/%m/%Y %H:%M") if d.certified_at else "",
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
        writer.writerow([
            d.certified_at.strftime("%d/%m/%Y %H:%M:%S") if d.certified_at else "",
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

