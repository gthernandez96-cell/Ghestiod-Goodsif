"""API v1 — Reportes Financieros, Comerciales y BI 360°."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date, desc, or_
from sqlalchemy.orm import selectinload
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
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.payments),
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.branch),
            selectinload(SaleOrderLine.sale).selectinload(SaleOrder.customer),
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
        sale_tot = float(sale.total or 0)
        if sale_tot <= 0:
            sale_tot = sum(float(l.total or 0) for l in (sale.lines or []))

        # Montos de pagos registrados
        paid_credit = sum(float(p.amount or 0) for p in (sale.payments or []) if getattr(p, "payment_method", "") == "credit")
        paid_contado = sum(float(p.amount or 0) for p in (sale.payments or []) if getattr(p, "payment_method", "") != "credit")
        unpaid_due = max(0.0, float(sale.amount_due or 0))

        # Saldo pendiente no cubierto o pago registrado como crédito
        total_credit_amt = paid_credit
        if unpaid_due > 0.001:
            total_credit_amt += unpaid_due
        elif sale.sale_type == SaleType.CREDIT and total_credit_amt <= 0:
            total_credit_amt = sale_tot

        total_contado_amt = paid_contado

        # Si no hay pagos registrados en la orden
        if not (sale.payments or []):
            if sale.sale_type == SaleType.CREDIT or unpaid_due > 0.001:
                total_credit_amt = sale_tot
                total_contado_amt = 0.0
            else:
                total_contado_amt = sale_tot
                total_credit_amt = 0.0

        total_tracked = total_credit_amt + total_contado_amt
        if total_tracked > 0:
            cred_ratio = max(0.0, min(1.0, total_credit_amt / total_tracked))
        elif sale.sale_type == SaleType.CREDIT or unpaid_due > 0.001:
            cred_ratio = 1.0
        else:
            cred_ratio = 0.0

        f_credito = round(tot_venta * cred_ratio, 2)
        f_contado = round(tot_venta - f_credito, 2)
        tipo_venta = "Crédito" if cred_ratio > 0.001 else "Contado"


        # Cliente CF o nombre
        raw_name = (sale.customer_name or (sale.customer.name if sale.customer else "") or "").strip()
        if not raw_name or raw_name.upper() in {"CF", "C/F", "CONSUMIDOR FINAL", "CLIENTE FINAL", "N/A"}:
            client_name = "CF"
        else:
            client_name = raw_name

        raw_id = (sale.customer_nit or (sale.customer.tax_id if sale.customer else "") or (sale.customer.code if sale.customer and sale.customer.code else "") or "").strip()
        if not raw_id or raw_id.upper() in {"CF", "C/F", "CONSUMIDOR FINAL"}:
            client_id = "CF"
        else:
            client_id = raw_id

        fecha_str = sale.sale_date.strftime("%d/%m/%Y %H:%M") if sale.sale_date else ""
        sucursal_str = sale.branch.name if (sale.branch and sale.branch.name) else "Sucursal Central"
        categoria_str = prod.category.name if (prod and prod.category and prod.category.name) else "General"
        barcode_str = line.product_barcode or (prod.barcode if prod else "") or ""
        articulo_str = line.product_name or (prod.name if prod else "N/A")
        ticket_str = sale.fel_number or sale.number or ""

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
        "total": summary["total_records"]
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
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
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


