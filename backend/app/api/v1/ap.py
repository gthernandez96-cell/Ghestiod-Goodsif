"""API v1 — Cuentas por Pagar (AP) & Control de Proveedores 360°."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc, or_, case
from sqlalchemy.orm import selectinload, joinedload
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid
import io
import csv
import time

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.ap import SupplierBill, SupplierBillPayment
from app.models.partner import Partner, PartnerType
from app.models.company import Company
from app.models.purchase import PurchaseOrder

router = APIRouter(prefix="/ap")

# Cache para limitar sincronización en segundo plano (máximo 1 vez cada 10 minutos por empresa)
_LAST_AP_SYNC: dict[str, float] = {}
_AP_SYNC_INTERVAL_SECONDS = 600


async def _sync_purchases_to_bills(db: AsyncSession, company_id: uuid.UUID):
    """Sincronizar automáticamente cualquier orden de compra reciente que no tenga su factura en Cuentas por Pagar."""
    comp_key = str(company_id)
    now_ts = time.time()
    if now_ts - _LAST_AP_SYNC.get(comp_key, 0) < _AP_SYNC_INTERVAL_SECONDS:
        return
    _LAST_AP_SYNC[comp_key] = now_ts

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        po_stmt = select(
            PurchaseOrder.id,
            PurchaseOrder.number,
            PurchaseOrder.supplier_ref,
            PurchaseOrder.supplier_id,
            PurchaseOrder.order_date,
            PurchaseOrder.payment_terms,
            PurchaseOrder.total
        ).where(
            PurchaseOrder.company_id == company_id,
            PurchaseOrder.is_deleted == False,
            PurchaseOrder.created_at >= cutoff
        )
        po_res = await db.execute(po_stmt)
        purchases = po_res.all()

        if not purchases:
            return

        bills_stmt = select(SupplierBill.number, SupplierBill.notes).where(
            SupplierBill.company_id == company_id,
            SupplierBill.is_deleted == False,
            SupplierBill.created_at >= cutoff
        )
        bills_res = await db.execute(bills_stmt)
        bills_data = bills_res.all()
        
        existing_numbers = {b[0] for b in bills_data if b[0]}
        existing_notes = [b[1] for b in bills_data if b[1]]

        synced_any = False
        for po in purchases:
            po_id, po_number, po_supplier_ref, po_supplier_id, po_order_date, po_payment_terms, po_total = po
            bill_num = po_supplier_ref or f"FAC-{po_number}"
            
            already_exists = False
            if bill_num in existing_numbers:
                already_exists = True
            else:
                for n in existing_notes:
                    if po_number in n:
                        already_exists = True
                        break

            if not already_exists and po_supplier_id:
                order_dt = po_order_date or datetime.now(timezone.utc)
                credit_days = 30
                if po_payment_terms:
                    terms_str = str(po_payment_terms).lower()
                    if "15" in terms_str:
                        credit_days = 15
                    elif "30" in terms_str:
                        credit_days = 30
                    elif "60" in terms_str:
                        credit_days = 60
                    elif "contado" in terms_str or "cash" in terms_str:
                        credit_days = 0

                due_date = order_dt + timedelta(days=credit_days)
                new_bill = SupplierBill(
                    company_id=company_id,
                    supplier_id=po_supplier_id,
                    number=bill_num,
                    bill_date=order_dt,
                    due_date=due_date,
                    total=po_total or 0,
                    balance=po_total or 0,
                    state="open",
                    notes=f"Compra {po_number}" + (f" - Ref: {po_supplier_ref}" if po_supplier_ref else "")
                )
                db.add(new_bill)
                existing_numbers.add(bill_num)
                existing_notes.append(new_bill.notes)
                synced_any = True

        if synced_any:
            await db.commit()
    except Exception:
        pass


# ===== SCHEMAS =====
class SupplierBillCreateIn(BaseModel):
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_nit: Optional[str] = "CF"
    number: str
    bill_date: Optional[str] = None
    credit_days: int = 30
    total: float
    notes: Optional[str] = None


class APPaymentIn(BaseModel):
    amount: float
    payment_method: str = "transfer"  # transfer, check, cash, card
    reference: Optional[str] = None
    notes: Optional[str] = None


# ===== 1. KPIS =====
@router.get("/kpis")
async def get_ap_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Métricas ejecutivas de Cuentas por Pagar y Deuda optimizadas en una sola agregación."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_purchases_to_bills(db, c_uuid)

    now = datetime.now(timezone.utc)
    in_7_days = now + timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Agregación unificada en una sola consulta SQL
    kpi_stmt = select(
        func.coalesce(func.sum(SupplierBill.balance), 0).label("total_debt"),
        func.count(SupplierBill.id).label("total_open_bills"),
        func.coalesce(func.sum(case((and_(SupplierBill.due_date >= now, SupplierBill.due_date <= in_7_days), 1), else_=0)), 0).label("due_soon_count"),
        func.coalesce(func.sum(case((SupplierBill.due_date < now, 1), else_=0)), 0).label("overdue_count"),
        func.coalesce(func.sum(case((SupplierBill.due_date < now, SupplierBill.balance), else_=0)), 0).label("overdue_amount"),
    ).where(
        SupplierBill.company_id == c_uuid,
        SupplierBill.balance > 0,
        SupplierBill.is_deleted == False
    )
    kpi_res = await db.execute(kpi_stmt)
    kpi_row = kpi_res.one()

    # 2. Pagos realizados este mes
    paid_stmt = (
        select(func.coalesce(func.sum(SupplierBillPayment.amount), 0))
        .join(SupplierBill, SupplierBillPayment.bill_id == SupplierBill.id)
        .where(
            SupplierBill.company_id == c_uuid,
            SupplierBillPayment.payment_date >= month_start
        )
    )
    paid_res = await db.execute(paid_stmt)
    paid_this_month = float(paid_res.scalar() or 0.0)

    return {
        "total_debt": float(kpi_row.total_debt or 0.0),
        "total_open_bills": int(kpi_row.total_open_bills or 0),
        "due_soon_count": int(kpi_row.due_soon_count or 0),
        "overdue_count": int(kpi_row.overdue_count or 0),
        "overdue_amount": float(kpi_row.overdue_amount or 0.0),
        "paid_this_month": paid_this_month,
    }


# ===== 2. LIST BILLS =====
@router.get("/bills")
async def list_supplier_bills(
    status: Optional[str] = Query(None),  # 'all', 'open', 'due_soon', 'overdue', 'paid'
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar facturas de proveedores con semáforo de vencimiento filtradas en SQL."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    now = datetime.now(timezone.utc)
    in_7_days = now + timedelta(days=7)

    stmt = (
        select(SupplierBill)
        .options(joinedload(SupplierBill.supplier))
        .where(
            SupplierBill.company_id == c_uuid,
            SupplierBill.is_deleted == False
        )
    )

    if status and status != "all":
        if status == "open":
            stmt = stmt.where(SupplierBill.balance > 0)
        elif status == "paid":
            stmt = stmt.where(SupplierBill.balance <= 0)
        elif status == "overdue":
            stmt = stmt.where(SupplierBill.balance > 0, SupplierBill.due_date < now)
        elif status == "due_soon":
            stmt = stmt.where(
                SupplierBill.balance > 0,
                SupplierBill.due_date >= now,
                SupplierBill.due_date <= in_7_days
            )

    if search:
        search_term = f"%{search.strip()}%"
        stmt = stmt.outerjoin(SupplierBill.supplier).where(
            or_(
                SupplierBill.number.ilike(search_term),
                Partner.name.ilike(search_term),
                Partner.nit.ilike(search_term)
            )
        )

    stmt = stmt.order_by(SupplierBill.due_date.asc())

    res = await db.execute(stmt)
    bills = res.scalars().unique().all()

    items = []
    for b in bills:
        bal = float(b.balance or 0.0)
        tot = float(b.total or 0.0)

        d_status = "paid"
        days_diff = 0

        if bal > 0:
            if b.due_date:
                due_dt = b.due_date
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                diff = (due_dt.date() - now.date()).days
                days_diff = diff
                if diff < 0:
                    d_status = "overdue"
                elif diff <= 7:
                    d_status = "due_soon"
                else:
                    d_status = "on_time"
            else:
                d_status = "on_time"

        s_name = b.supplier.name if b.supplier else "Proveedor Genérico"
        s_nit = b.supplier.nit if b.supplier else "CF"

        items.append({
            "id": str(b.id),
            "number": b.number,
            "supplier_id": str(b.supplier_id) if b.supplier_id else "",
            "supplier_name": s_name,
            "supplier_nit": s_nit,
            "bill_date": b.bill_date.strftime("%d/%m/%Y") if b.bill_date else "",
            "due_date": b.due_date.strftime("%d/%m/%Y") if b.due_date else "Sin fecha",
            "days_diff": days_diff,
            "total": tot,
            "balance": bal,
            "status": d_status,
            "notes": b.notes or "",
            "payments_count": 0
        })

    return items


# ===== 3. GET SINGLE BILL DETAIL =====
@router.get("/bills/{bill_id}")
async def get_supplier_bill(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle de factura de proveedor con su historial de abonos."""
    try:
        b_uuid = uuid.UUID(bill_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de factura inválido")

    stmt = (
        select(SupplierBill)
        .options(
            selectinload(SupplierBill.supplier),
            selectinload(SupplierBill.payments)
        )
        .where(
            SupplierBill.id == b_uuid,
            SupplierBill.company_id == current_user.company_id
        )
    )
    res = await db.execute(stmt)
    bill = res.scalar_one_or_none()
    if not bill:
        raise HTTPException(status_code=404, detail="Factura de proveedor no encontrada")

    return {
        "id": str(bill.id),
        "number": bill.number,
        "supplier_name": bill.supplier.name if bill.supplier else "Proveedor",
        "supplier_nit": bill.supplier.nit if bill.supplier else "CF",
        "bill_date": bill.bill_date.strftime("%d/%m/%Y") if bill.bill_date else "",
        "due_date": bill.due_date.strftime("%d/%m/%Y") if bill.due_date else "",
        "total": float(bill.total or 0.0),
        "balance": float(bill.balance or 0.0),
        "state": bill.state,
        "notes": bill.notes or "",
        "payments": [
            {
                "id": str(p.id),
                "amount": float(p.amount),
                "date": p.payment_date.strftime("%d/%m/%Y %H:%M") if p.payment_date else "",
                "method": p.payment_method,
                "reference": p.reference or "S/R",
                "notes": p.notes or ""
            }
            for p in bill.payments
        ]
    }


# ===== 4. CREATE SUPPLIER BILL =====
@router.post("/bills")
async def create_supplier_bill(
    data: SupplierBillCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Registrar una nueva factura por pagar a proveedor."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    # Resolver Proveedor
    supplier_uuid = None
    if data.supplier_id:
        try:
            supplier_uuid = uuid.UUID(data.supplier_id)
        except ValueError:
            pass

    if not supplier_uuid and data.supplier_name:
        stmt_p = select(Partner).where(
            Partner.name == data.supplier_name,
            Partner.company_id == c_uuid
        ).limit(1)
        res_p = await db.execute(stmt_p)
        supplier = res_p.scalar_one_or_none()
        if not supplier:
            supplier = Partner(
                company_id=c_uuid,
                partner_type=PartnerType.SUPPLIER,
                name=data.supplier_name,
                nit=data.supplier_nit or "CF",
                is_company=True
            )
            db.add(supplier)
            await db.flush()
        supplier_uuid = supplier.id

    if not supplier_uuid:
        # Fallback a primer proveedor
        s_first = await db.execute(select(Partner.id).where(Partner.company_id == c_uuid).limit(1))
        supplier_uuid = s_first.scalar_one_or_none()

    # Fechas
    now = datetime.now(timezone.utc)
    b_date = now
    if data.bill_date:
        try:
            b_date = datetime.strptime(data.bill_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass

    due_date = b_date + timedelta(days=data.credit_days or 30)

    bill = SupplierBill(
        company_id=c_uuid,
        supplier_id=supplier_uuid,
        number=data.number,
        bill_date=b_date,
        due_date=due_date,
        total=Decimal(str(data.total)),
        balance=Decimal(str(data.total)),
        state="open",
        notes=data.notes
    )
    db.add(bill)
    await db.commit()
    await db.refresh(bill)

    return {
        "id": str(bill.id),
        "number": bill.number,
        "total": float(bill.total),
        "balance": float(bill.balance),
        "due_date": bill.due_date.strftime("%d/%m/%Y") if bill.due_date else ""
    }


# ===== 5. REGISTER PAYMENT / ABONO =====
@router.post("/bills/{bill_id}/payments")
async def register_bill_payment(
    bill_id: str,
    data: APPaymentIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Registrar un abono o liquidación a una factura de proveedor."""
    try:
        b_uuid = uuid.UUID(bill_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de factura inválido")

    stmt = select(SupplierBill).where(
        SupplierBill.id == b_uuid,
        SupplierBill.company_id == current_user.company_id
    )
    res = await db.execute(stmt)
    bill = res.scalar_one_or_none()
    if not bill:
        raise HTTPException(status_code=404, detail="Factura de proveedor no encontrada")

    if bill.balance <= 0:
        raise HTTPException(status_code=400, detail="Esta factura ya está completamente pagada.")

    amt = Decimal(str(data.amount))
    if amt <= 0:
        raise HTTPException(status_code=400, detail="El monto del abono debe ser mayor a 0.")

    if amt > bill.balance:
        amt = bill.balance  # Ajustar a saldo exacto si se excede

    # Registrar el pago
    payment = SupplierBillPayment(
        bill_id=bill.id,
        amount=amt,
        payment_date=datetime.now(timezone.utc),
        payment_method=data.payment_method,
        reference=data.reference,
        notes=data.notes
    )
    db.add(payment)

    # Actualizar saldo
    prev_balance = bill.balance
    bill.balance = bill.balance - amt
    if bill.balance <= 0:
        bill.balance = Decimal("0")
        bill.state = "paid"

    await db.commit()
    await db.refresh(payment)
    await db.refresh(bill)

    return {
        "status": "success",
        "bill_id": str(bill.id),
        "payment_id": str(payment.id),
        "previous_balance": float(prev_balance),
        "paid_amount": float(amt),
        "new_balance": float(bill.balance),
        "state": bill.state,
        "message": "¡Abono registrado exitosamente!"
    }


# ===== 6. PAYMENT VOUCHER DETAIL =====
@router.get("/payments/{payment_id}/voucher")
async def get_payment_voucher(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener datos para comprobante/recibo imprimible de pago a proveedor."""
    try:
        p_uuid = uuid.UUID(payment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de pago inválido")

    stmt = (
        select(SupplierBillPayment)
        .options(
            selectinload(SupplierBillPayment.bill).selectinload(SupplierBill.supplier),
            selectinload(SupplierBillPayment.bill).selectinload(SupplierBill.payments)
        )
        .where(SupplierBillPayment.id == p_uuid)
    )
    res = await db.execute(stmt)
    pmt = res.scalar_one_or_none()
    if not pmt or not pmt.bill or pmt.bill.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Comprobante de pago no encontrado")

    # Calcular saldos relativos al pago
    bill = pmt.bill
    supplier = bill.supplier

    # Empresa
    comp_res = await db.execute(select(Company).where(Company.id == current_user.company_id))
    company = comp_res.scalar_one_or_none()

    return {
        "voucher_id": str(pmt.id),
        "voucher_number": f"VOUCH-{str(pmt.id)[:8].upper()}",
        "payment_date": pmt.payment_date.strftime("%d/%m/%Y %H:%M") if pmt.payment_date else "",
        "payment_method": pmt.payment_method,
        "reference": pmt.reference or "N/A",
        "notes": pmt.notes or "",
        "amount_paid": float(pmt.amount),
        "bill": {
            "id": str(bill.id),
            "number": bill.number,
            "total": float(bill.total),
            "current_balance": float(bill.balance),
            "bill_date": bill.bill_date.strftime("%d/%m/%Y") if bill.bill_date else "",
            "due_date": bill.due_date.strftime("%d/%m/%Y") if bill.due_date else ""
        },
        "supplier": {
            "name": supplier.name if supplier else "Proveedor",
            "nit": supplier.nit if supplier else "CF",
            "phone": supplier.phone if supplier else "",
            "email": supplier.email if supplier else "",
            "address": supplier.address if supplier else ""
        },
        "company": {
            "name": company.name if company else "POS Empresarial S.A.",
            "nit": company.nit if company else "CF",
            "phone": company.phone if company else "",
            "address": company.address if company else ""
        },
        "user_name": f"{getattr(current_user, 'first_name', '')} {getattr(current_user, 'last_name', '')}".strip() or current_user.email
    }


# ===== 7. AGING REPORT (ANTIGÜEDAD DE SALDOS) =====
@router.get("/aging")
async def get_ap_aging_report(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Reporte ejecutivo de Antigüedad de Saldos por Proveedor (0-30, 31-60, 61-90, 90+)."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    now = datetime.now(timezone.utc)

    stmt = (
        select(SupplierBill)
        .options(joinedload(SupplierBill.supplier))
        .where(
            SupplierBill.company_id == c_uuid,
            SupplierBill.balance > 0,
            SupplierBill.is_deleted == False
        )
    )
    res = await db.execute(stmt)
    bills = res.scalars().unique().all()

    # Buckets globales
    summary = {
        "current": 0.0,    # Al día / No vencido
        "range_1_30": 0.0, # 1 - 30 días de mora
        "range_31_60": 0.0,# 31 - 60 días
        "range_61_90": 0.0,# 61 - 90 días
        "range_90_plus": 0.0,# > 90 días
        "total": 0.0
    }

    # Agrupado por proveedor
    suppliers_dict = {}

    for b in bills:
        bal = float(b.balance or 0.0)
        due_dt = b.due_date
        if due_dt:
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            days_overdue = (now.date() - due_dt.date()).days
        else:
            days_overdue = 0

        sup_id = str(b.supplier_id) if b.supplier_id else "unknown"
        sup_name = b.supplier.name if b.supplier else "Proveedor no identificado"
        sup_nit = b.supplier.nit if b.supplier else "CF"

        if sup_id not in suppliers_dict:
            suppliers_dict[sup_id] = {
                "supplier_id": sup_id,
                "supplier_name": sup_name,
                "supplier_nit": sup_nit,
                "current": 0.0,
                "range_1_30": 0.0,
                "range_31_60": 0.0,
                "range_61_90": 0.0,
                "range_90_plus": 0.0,
                "total": 0.0,
                "bills_count": 0
            }

        target = suppliers_dict[sup_id]
        target["bills_count"] += 1
        target["total"] += bal
        summary["total"] += bal

        if days_overdue <= 0:
            target["current"] += bal
            summary["current"] += bal
        elif 1 <= days_overdue <= 30:
            target["range_1_30"] += bal
            summary["range_1_30"] += bal
        elif 31 <= days_overdue <= 60:
            target["range_31_60"] += bal
            summary["range_31_60"] += bal
        elif 61 <= days_overdue <= 90:
            target["range_61_90"] += bal
            summary["range_61_90"] += bal
        else:
            target["range_90_plus"] += bal
            summary["range_90_plus"] += bal

    supplier_list = list(suppliers_dict.values())
    supplier_list.sort(key=lambda x: x["total"], reverse=True)

    return {
        "summary": summary,
        "suppliers": supplier_list
    }


# ===== 8. EXPORT CSV =====
@router.get("/export")
async def export_ap_csv(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Descargar estado general de Cuentas por Pagar en formato CSV/Excel."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    now = datetime.now(timezone.utc)

    stmt = (
        select(SupplierBill)
        .options(joinedload(SupplierBill.supplier))
        .where(
            SupplierBill.company_id == c_uuid,
            SupplierBill.is_deleted == False
        )
        .order_by(SupplierBill.due_date.asc())
    )
    res = await db.execute(stmt)
    bills = res.scalars().unique().all()

    output = io.StringIO()
    output.write('\ufeff')  # BOM UTF-8 para compatibilidad perfecta con Excel
    writer = csv.writer(output)

    # Encabezados
    writer.writerow([
        "Proveedor",
        "NIT",
        "No. Factura",
        "Fecha Emision",
        "Fecha Vencimiento",
        "Dias Mora / Restantes",
        "Monto Total (Q)",
        "Saldo Pendiente (Q)",
        "Estado",
        "Notas"
    ])

    for b in bills:
        bal = float(b.balance or 0.0)
        due_dt = b.due_date
        days_diff = 0
        status_txt = "PAGADA"
        if bal > 0:
            if due_dt:
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                diff = (due_dt.date() - now.date()).days
                days_diff = diff
                if diff < 0:
                    status_txt = f"VENCIDA ({abs(diff)} dias)"
                elif diff <= 7:
                    status_txt = f"POR VENCER ({diff} dias)"
                else:
                    status_txt = f"AL DIA ({diff} dias)"
            else:
                status_txt = "AL DIA"

        writer.writerow([
            b.supplier.name if b.supplier else "N/A",
            b.supplier.nit if b.supplier else "CF",
            b.number,
            b.bill_date.strftime("%d/%m/%Y") if b.bill_date else "",
            b.due_date.strftime("%d/%m/%Y") if b.due_date else "",
            days_diff,
            f"{float(b.total or 0.0):.2f}",
            f"{bal:.2f}",
            status_txt,
            b.notes or ""
        ])

    csv_data = output.getvalue()
    filename = f"cuentas_por_pagar_{now.strftime('%Y%m%d_%H%M')}.csv"

    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

