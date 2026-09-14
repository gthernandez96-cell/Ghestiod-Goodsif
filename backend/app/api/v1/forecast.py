"""API v1 — Previsión de Demanda y Reorden Automatizado (AI Demand Forecasting)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid
import math

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SaleState
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, PurchaseState
from app.models.product import Product, ProductCategory
from app.models.inventory import StockQuant
from app.models.company import Branch, Warehouse
from app.models.partner import Partner, PartnerType

router = APIRouter(prefix="/forecast")


# ===== SCHEMAS =====
class ReorderItemIn(BaseModel):
    product_id: str
    product_name: str
    qty: float
    unit_price: float


class GeneratePODraftIn(BaseModel):
    supplier_id: str
    branch_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    items: List[ReorderItemIn]
    notes: Optional[str] = None


def _generate_po_number() -> str:
    now = datetime.now()
    seq = str(uuid.uuid4().int)[:6]
    return f"OC-AUTO-{now.strftime('%Y%m%d')}-{seq}"


# ===== HELPER CALCULATIONS =====
async def _calculate_demand_metrics(
    db: AsyncSession,
    company_id: uuid.UUID,
    branch_id: Optional[str] = None,
    days: int = 30,
    target_cover_days: int = 15,
) -> Dict[str, Any]:
    """Calcula las métricas de rotación, días de inventario y reorden para los productos."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    # 1. Ventas históricas agrupadas por producto
    sales_stmt = (
        select(
            SaleOrderLine.product_id,
            func.sum(SaleOrderLine.qty).label("units_sold")
        )
        .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
        .where(
            SaleOrder.company_id == company_id,
            SaleOrder.sale_date >= cutoff_date,
            SaleOrder.state.in_([SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID])
        )
    )

    if branch_id and branch_id.strip():
        try:
            sales_stmt = sales_stmt.where(SaleOrder.branch_id == uuid.UUID(branch_id.strip()))
        except ValueError:
            pass

    sales_stmt = sales_stmt.group_by(SaleOrderLine.product_id)
    sales_res = await db.execute(sales_stmt)
    sales_map = {row.product_id: float(row.units_sold or 0) for row in sales_res.all()}

    # 2. Existencias actuales por producto
    stock_stmt = (
        select(
            StockQuant.product_id,
            func.sum(StockQuant.qty_on_hand).label("stock_qty")
        )
        .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
        .join(Branch, Warehouse.branch_id == Branch.id)
        .where(
            Branch.company_id == company_id,
            Warehouse.is_deleted == False
        )
    )

    if branch_id and branch_id.strip():
        try:
            stock_stmt = stock_stmt.where(Warehouse.branch_id == uuid.UUID(branch_id.strip()))
        except ValueError:
            pass

    stock_stmt = stock_stmt.group_by(StockQuant.product_id)
    stock_res = await db.execute(stock_stmt)
    stock_map = {row.product_id: float(row.stock_qty or 0) for row in stock_res.all()}

    # 3. Lista de productos activos
    prod_stmt = (
        select(Product)
        .options(
            selectinload(Product.category),
            selectinload(Product.supplier)
        )
        .where(
            Product.company_id == company_id,
            Product.is_active == True,
            Product.is_deleted == False
        )
        .order_by(Product.name.asc())
    )
    prod_res = await db.execute(prod_stmt)
    products = prod_res.scalars().all()

    items = []
    critical_count = 0
    warning_count = 0
    healthy_count = 0
    overstock_count = 0
    dead_stock_count = 0
    total_suggested_cost = 0.0
    dead_stock_capital = 0.0

    for p in products:
        u_sold = sales_map.get(p.id, 0.0)
        current_stk = stock_map.get(p.id, 0.0)
        cost = float(p.cost_price or 0.0)
        min_stk = float(p.min_stock or 0.0)

        # Velocidad promedio diaria
        daily_vel = round(u_sold / max(days, 1), 2)

        # Días de cobertura
        if daily_vel > 0:
            days_of_stock = round(current_stk / daily_vel, 1)
            if days_of_stock <= 3.0:
                risk_level = "critical"
                critical_count += 1
            elif days_of_stock <= 7.0:
                risk_level = "warning"
                warning_count += 1
            elif days_of_stock <= 30.0:
                risk_level = "healthy"
                healthy_count += 1
            else:
                risk_level = "overstock"
                overstock_count += 1
        else:
            if current_stk > 0:
                risk_level = "dead_stock"
                dead_stock_count += 1
                dead_stock_capital += round(current_stk * cost, 2)
                days_of_stock = 999.0
            else:
                risk_level = "zero_stock"
                days_of_stock = 0.0

        # Cantidad sugerida a reordenar
        suggested_qty = 0.0
        if risk_level in ["critical", "warning"]:
            needed_for_target = target_cover_days * daily_vel
            suggested_qty = max(0.0, math.ceil(needed_for_target - current_stk))
            if min_stk > 0 and (current_stk + suggested_qty) < min_stk:
                suggested_qty = max(suggested_qty, math.ceil(min_stk - current_stk))
        elif risk_level == "zero_stock" and min_stk > 0:
            suggested_qty = min_stk

        est_cost = round(suggested_qty * cost, 2)
        total_suggested_cost += est_cost

        supplier_info = None
        if p.supplier:
            supplier_info = {
                "id": str(p.supplier.id),
                "name": p.supplier.name,
                "nit": p.supplier.nit or "C/F",
                "phone": p.supplier.phone or ""
            }

        items.append({
            "product_id": str(p.id),
            "product_name": p.name,
            "barcode": p.barcode or p.internal_ref or "",
            "category_name": p.category.name if p.category else "General",
            "current_stock": current_stk,
            "units_sold": u_sold,
            "daily_velocity": daily_vel,
            "days_of_stock": days_of_stock,
            "risk_level": risk_level,
            "min_stock": min_stk,
            "cost_price": cost,
            "sale_price": float(p.sale_price or 0.0),
            "suggested_reorder_qty": suggested_qty,
            "estimated_cost": est_cost,
            "supplier": supplier_info
        })

    # Ordenar por prioridad: primero críticos, luego advertencias, luego por velocidad
    risk_priority = {"critical": 0, "warning": 1, "zero_stock": 2, "dead_stock": 3, "healthy": 4, "overstock": 5}
    items.sort(key=lambda x: (risk_priority.get(x["risk_level"], 99), -x["daily_velocity"]))

    return {
        "analysis_period_days": days,
        "target_cover_days": target_cover_days,
        "summary": {
            "total_products": len(products),
            "critical_count": critical_count,
            "warning_count": warning_count,
            "healthy_count": healthy_count,
            "overstock_count": overstock_count,
            "dead_stock_count": dead_stock_count,
            "total_suggested_cost": round(total_suggested_cost, 2),
            "dead_stock_capital": round(dead_stock_capital, 2)
        },
        "items": items
    }


# ===== ENDPOINTS =====
@router.get("/demand")
async def get_demand_forecast(
    branch_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=180),
    target_cover_days: int = Query(15, ge=5, le=90),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Análisis detallado de velocidad de venta y proyección de existencias."""
    return await _calculate_demand_metrics(
        db=db,
        company_id=current_user.company_id,
        branch_id=branch_id,
        days=days,
        target_cover_days=target_cover_days
    )


@router.get("/reorder-suggestions")
async def get_reorder_suggestions(
    branch_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=180),
    target_cover_days: int = Query(15, ge=5, le=90),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Sugerencias de reorden agrupadas por proveedor para emisión de órdenes de compra."""
    data = await _calculate_demand_metrics(
        db=db,
        company_id=current_user.company_id,
        branch_id=branch_id,
        days=days,
        target_cover_days=target_cover_days
    )

    # Filtrar solo productos con necesidad de reposición
    reorder_items = [
        item for item in data["items"]
        if item["suggested_reorder_qty"] > 0
    ]

    # Agrupar por proveedor
    groups_dict: Dict[str, Dict[str, Any]] = {}
    for item in reorder_items:
        sup = item["supplier"]
        sup_id = sup["id"] if sup else "unassigned"
        sup_name = sup["name"] if sup else "Sin Proveedor Asignado"
        sup_nit = sup["nit"] if sup else "N/A"

        if sup_id not in groups_dict:
            groups_dict[sup_id] = {
                "supplier_id": sup_id,
                "supplier_name": sup_name,
                "supplier_nit": sup_nit,
                "products_count": 0,
                "total_estimated_cost": 0.0,
                "items": []
            }

        groups_dict[sup_id]["products_count"] += 1
        groups_dict[sup_id]["total_estimated_cost"] = round(
            groups_dict[sup_id]["total_estimated_cost"] + item["estimated_cost"], 2
        )
        groups_dict[sup_id]["items"].append(item)

    groups = sorted(groups_dict.values(), key=lambda g: g["total_estimated_cost"], reverse=True)

    return {
        "summary": data["summary"],
        "total_reorder_products": len(reorder_items),
        "supplier_groups": groups
    }


@router.post("/generate-po")
async def generate_po_from_forecast(
    payload: GeneratePODraftIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crea una orden de compra en estado borrador a partir de las sugerencias del reorden."""
    if not payload.items:
        raise HTTPException(status_code=400, detail="Debe incluir al menos un producto a ordenar.")

    # 1. Resolver sucursal y bodega
    branch_uuid = None
    if payload.branch_id and payload.branch_id.strip():
        try:
            branch_uuid = uuid.UUID(payload.branch_id.strip())
        except ValueError:
            pass

    if not branch_uuid:
        # Usar la primera sucursal activa
        b_stmt = select(Branch).where(Branch.company_id == current_user.company_id, Branch.is_deleted == False)
        b_res = await db.execute(b_stmt)
        branch = b_res.scalars().first()
        if not branch:
            raise HTTPException(status_code=400, detail="No se encontró ninguna sucursal activa.")
        branch_uuid = branch.id

    # Bodega
    warehouse_uuid = None
    if payload.warehouse_id and payload.warehouse_id.strip():
        try:
            warehouse_uuid = uuid.UUID(payload.warehouse_id.strip())
        except ValueError:
            pass

    if not warehouse_uuid:
        wh_stmt = select(Warehouse).where(
            Warehouse.branch_id == branch_uuid,
            Warehouse.is_deleted == False
        ).order_by(Warehouse.is_default.desc())
        wh_res = await db.execute(wh_stmt)
        warehouse = wh_res.scalars().first()
        if not warehouse:
            raise HTTPException(status_code=400, detail="No se encontró ninguna bodega en la sucursal seleccionada.")
        warehouse_uuid = warehouse.id

    # 2. Proveedor
    supplier_uuid = None
    if payload.supplier_id and payload.supplier_id != "unassigned":
        try:
            supplier_uuid = uuid.UUID(payload.supplier_id)
        except ValueError:
            pass

    if not supplier_uuid:
        # Buscar o crear un proveedor genérico si no se especificó uno
        p_stmt = select(Partner).where(
            Partner.company_id == current_user.company_id,
            Partner.partner_type.in_([PartnerType.SUPPLIER, PartnerType.BOTH])
        )
        p_res = await db.execute(p_stmt)
        default_sup = p_res.scalars().first()
        if default_sup:
            supplier_uuid = default_sup.id
        else:
            new_sup = Partner(
                company_id=current_user.company_id,
                name="Proveedor General de Compras",
                nit="CF",
                partner_type=PartnerType.SUPPLIER
            )
            db.add(new_sup)
            await db.flush()
            supplier_uuid = new_sup.id

    # 3. Crear PurchaseOrder
    po_number = _generate_po_number()
    po = PurchaseOrder(
        company_id=current_user.company_id,
        branch_id=branch_uuid,
        warehouse_id=warehouse_uuid,
        supplier_id=supplier_uuid,
        user_id=current_user.id,
        number=po_number,
        state=PurchaseState.DRAFT,
        order_date=datetime.now(timezone.utc),
        notes=payload.notes or "Generado automáticamente por el Asistente de Reorden Inteligente."
    )
    db.add(po)
    await db.flush()

    # 4. Crear líneas de compra
    total_subtotal = Decimal("0.0")
    total_tax = Decimal("0.0")
    total_amount = Decimal("0.0")

    for item in payload.items:
        try:
            p_uuid = uuid.UUID(item.product_id)
        except ValueError:
            continue

        qty = Decimal(str(item.qty))
        unit_price = Decimal(str(item.unit_price))
        tax_rate = Decimal("12.0")

        gross = qty * unit_price
        # Asumiendo precio incluye IVA (estándar GT) o se calcula adicional:
        line_tax = gross * (tax_rate / Decimal("100.0"))
        line_total = gross

        line = PurchaseOrderLine(
            purchase_id=po.id,
            product_id=p_uuid,
            product_name=item.product_name,
            qty_ordered=qty,
            qty_pending=qty,
            qty_received=Decimal("0.0"),
            unit_price=unit_price,
            tax_rate=tax_rate,
            subtotal=gross - line_tax,
            tax_amount=line_tax,
            total=line_total
        )
        db.add(line)
        total_subtotal += (gross - line_tax)
        total_tax += line_tax
        total_amount += line_total

    po.subtotal = total_subtotal
    po.tax_amount = total_tax
    po.total = total_amount

    await db.commit()

    return {
        "status": "created",
        "po_id": str(po.id),
        "po_number": po.number,
        "total_lines": len(payload.items),
        "total_amount": float(total_amount),
        "message": f"Orden de compra {po.number} creada exitosamente en estado borrador."
    }


@router.get("/ai-summary")
async def get_ai_forecast_summary(
    branch_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Genera un diagnóstico ejecutivo con recomendaciones en lenguaje natural."""
    data = await _calculate_demand_metrics(
        db=db,
        company_id=current_user.company_id,
        branch_id=branch_id,
        days=days
    )

    summary = data["summary"]
    items = data["items"]

    critical_prods = [x for x in items if x["risk_level"] == "critical"][:3]
    dead_stock_prods = [x for x in items if x["risk_level"] == "dead_stock"][:3]

    insights = []

    # 1. Alerta de quiebre
    if critical_prods:
        names = ", ".join([f"**{p['product_name']}** ({p['current_stock']} unid restantes, {p['days_of_stock']} días de vida)" for p in critical_prods])
        insights.append(f"🚨 **Riesgo Inminente de Quiebre:** Tienes {len(critical_prods)} producto(s) que se agotarán en menos de 3 días: {names}. Se recomienda generar la orden de reposición hoy.")
    elif summary["warning_count"] > 0:
        insights.append(f"⚠️ **Monitoreo de Semana:** Hay {summary['warning_count']} producto(s) que alcanzarán su nivel crítico en los próximos 7 días.")
    else:
        insights.append("✅ **Inventario Estable:** No se detectan quiebres críticos inmediatos en este período de análisis.")

    # 2. Inversión sugerida
    if summary["total_suggested_cost"] > 0:
        insights.append(f"💰 **Presupuesto de Reposición Sugerido:** Se estima una inversión de **Q {summary['total_suggested_cost']:,.2f}** para cubrir 15 días de demanda óptima sin sobrecargar bodegas.")

    # 3. Capital inmovilizado
    if summary["dead_stock_capital"] > 0:
        d_names = ", ".join([f"**{p['product_name']}**" for p in dead_stock_prods])
        insights.append(f"📦 **Capital Inmovilizado:** Tienes **Q {summary['dead_stock_capital']:,.2f}** retenidos en productos sin rotación en los últimos {days} días ({d_names}). Sugerencia: Lanzar una promoción o descuento por paquete en el POS.")

    return {
        "status": "success",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline": f"Diagnóstico de Demanda — {summary['critical_count']} críticos | {summary['warning_count']} en alerta",
        "insights": insights,
        "summary": summary
    }
