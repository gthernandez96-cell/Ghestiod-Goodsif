"""API v1 — Cuentas por Cobrar (AR) & Control de Créditos a Clientes 360°."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid
import io
import csv

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.ar import CustomerInvoiceCredit, CustomerCreditPayment
from app.models.partner import Partner, PartnerType, CreditStatus
from app.models.company import Company
from app.models.sale import SaleOrder, SalePayment, SaleState, SaleType
from app.api.v1.partners import check_credit_payment_permission

router = APIRouter(prefix="/ar")


async def _sync_sales_to_credits(db: AsyncSession, company_id: uuid.UUID):
    """Sincronizar ventas al crédito, pre-ventas y clientes con saldo de crédito pendiente hacia Cuentas por Cobrar."""
    try:
        # 1. Obtener todas las cuentas por cobrar existentes
        credits_stmt = select(CustomerInvoiceCredit).where(
            CustomerInvoiceCredit.company_id == company_id,
            CustomerInvoiceCredit.is_deleted == False
        )
        credits_res = await db.execute(credits_stmt)
        existing_credits = credits_res.scalars().all()

        existing_numbers = {c.number for c in existing_credits if c.number}
        existing_sale_ids = {c.sale_id for c in existing_credits if c.sale_id}

        synced_any = False

        # Si alguna orden vinculada fue cancelada o rechazada, anular su cuenta por cobrar
        cancelled_sales_stmt = select(CustomerInvoiceCredit).join(
            SaleOrder, CustomerInvoiceCredit.sale_id == SaleOrder.id
        ).where(
            CustomerInvoiceCredit.company_id == company_id,
            CustomerInvoiceCredit.is_deleted == False,
            SaleOrder.state.in_([SaleState.CANCELLED, SaleState.REJECTED])
        )
        cancelled_invs = (await db.execute(cancelled_sales_stmt)).scalars().all()
        for cinv in cancelled_invs:
            cinv.is_deleted = True
            cinv.state = "cancelled"
            synced_any = True

        # 2. Sincronizar Ventas al Crédito y Pre-ventas con cliente identificado
        sales_stmt = (
            select(SaleOrder)
            .options(selectinload(SaleOrder.payments))
            .where(
                SaleOrder.company_id == company_id,
                SaleOrder.customer_id.isnot(None),
                SaleOrder.is_deleted == False
            )
        )
        sales_res = await db.execute(sales_stmt)
        sales = sales_res.scalars().all()

        for s in sales:
            if s.id in existing_sale_ids or s.number in existing_numbers:
                continue

            # Verificar si la venta tuvo pago explícito al CRÉDITO
            credit_paid = sum(float(p.amount or 0) for p in (s.payments or []) if p.payment_method and "credit" in str(p.payment_method).lower())
            
            # O si es una Pre-venta con cliente identificado (no cancelada ni rechazada)
            is_presale = (
                (s.sale_type == SaleType.PRESALE or (s.number and s.number.startswith("PRE-")))
                and s.state not in [SaleState.CANCELLED, SaleState.REJECTED]
                and (s.total or 0) > 0.01
            )

            if credit_paid > 0.01 or is_presale:
                tot_amount = Decimal(str(credit_paid if credit_paid > 0.01 else (s.total or 0)))
                bal_amount = Decimal(str(credit_paid if credit_paid > 0.01 else (s.amount_due if (s.amount_due is not None and s.amount_due > 0) else s.total or 0)))

                order_dt = s.sale_date or s.created_at or datetime.now(timezone.utc)
                credit_days = 30
                due_date = s.due_date or (order_dt + timedelta(days=credit_days))

                doc_notes = f"Pre-venta {s.number} - Cliente: {s.customer_name or 'Cliente'}" if is_presale else f"Venta al Crédito {s.number} - Cliente: {s.customer_name or 'Cliente'}"

                credit_inv = CustomerInvoiceCredit(
                    company_id=company_id,
                    customer_id=s.customer_id,
                    sale_id=s.id,
                    number=s.number or f"CRED-{str(s.id)[:8].upper()}",
                    issue_date=order_dt,
                    due_date=due_date,
                    total=tot_amount,
                    balance=bal_amount,
                    state="open" if bal_amount > 0 else "paid",
                    notes=doc_notes
                )
                db.add(credit_inv)
                existing_numbers.add(credit_inv.number)
                existing_sale_ids.add(s.id)
                existing_credits.append(credit_inv)
                synced_any = True

        # 3. Sincronizar Clientes con saldo pendiente de crédito en su cuenta (Partner.balance > 0)
        partners_stmt = select(Partner).where(
            Partner.company_id == company_id,
            Partner.balance > 0.01,
            Partner.is_deleted == False
        )
        partners_res = await db.execute(partners_stmt)
        debtor_partners = partners_res.scalars().all()

        for p in debtor_partners:
            p_balance = Decimal(str(p.balance or 0))
            if p_balance <= Decimal("0.01"):
                continue

            # Sumar cuentas de crédito abiertas de este cliente
            existing_p_bal = sum(
                c.balance for c in existing_credits 
                if c.customer_id == p.id and c.balance and c.balance > 0
            )

            diff_balance = p_balance - existing_p_bal
            if diff_balance > Decimal("0.01"):
                now_dt = datetime.now(timezone.utc)
                due_dt = now_dt + timedelta(days=p.payment_days or 30)
                inv_num = f"CRED-{str(p.id)[:6].upper()}"
                if inv_num in existing_numbers:
                    inv_num = f"CRED-{str(p.id)[:4].upper()}-{str(uuid.uuid4())[:4].upper()}"

                credit_inv = CustomerInvoiceCredit(
                    company_id=company_id,
                    customer_id=p.id,
                    number=inv_num,
                    issue_date=now_dt,
                    due_date=due_dt,
                    total=diff_balance,
                    balance=diff_balance,
                    state="open",
                    notes=f"Cuenta de Crédito - {p.name}"
                )
                db.add(credit_inv)
                existing_numbers.add(credit_inv.number)
                existing_credits.append(credit_inv)
                synced_any = True

        if synced_any:
            await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Error sincronizando ventas a créditos: {e}")


# ===== SCHEMAS =====
class CustomerInvoiceCreditCreateIn(BaseModel):
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_nit: Optional[str] = "CF"
    number: str
    issue_date: Optional[str] = None
    credit_days: int = 30
    total: float
    notes: Optional[str] = None


class ARPaymentIn(BaseModel):
    amount: float
    payment_method: str = "cash"  # cash, card, transfer, check
    reference: Optional[str] = None
    notes: Optional[str] = None


# ===== 1. KPIS =====
@router.get("/kpis")
async def get_ar_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Métricas ejecutivas de Cuentas por Cobrar y Cartera de Clientes unificadas por cliente."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_sales_to_credits(db, c_uuid)

    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    # 1. Total por cobrar pendiente por clientes
    p_stmt = select(Partner).where(
        Partner.company_id == c_uuid,
        Partner.balance > 0,
        Partner.is_deleted == False
    )
    p_res = await db.execute(p_stmt)
    debtor_partners = p_res.scalars().all()

    total_receivable = sum(float(p.balance or 0.0) for p in debtor_partners)
    total_debtors = len(debtor_partners)

    # 2. Calcular vencimientos por cliente (a partir de su primera venta al crédito)
    c_stmt = select(CustomerInvoiceCredit).where(
        CustomerInvoiceCredit.company_id == c_uuid,
        CustomerInvoiceCredit.balance > 0,
        CustomerInvoiceCredit.is_deleted == False
    ).order_by(CustomerInvoiceCredit.issue_date.asc())
    c_res = await db.execute(c_stmt)
    open_credits = c_res.scalars().all()

    credits_by_cust = {}
    for c in open_credits:
        cid = str(c.customer_id) if c.customer_id else None
        if cid and cid not in credits_by_cust:
            credits_by_cust[cid] = c.issue_date

    due_soon_count = 0
    overdue_count = 0
    overdue_amount = 0.0

    for p in debtor_partners:
        p_bal = float(p.balance or 0.0)
        first_dt = credits_by_cust.get(str(p.id), p.created_at or now)
        if first_dt and first_dt.tzinfo is None:
            first_dt = first_dt.replace(tzinfo=timezone.utc)
        
        due_dt = (first_dt + timedelta(days=p.payment_days or 30)) if first_dt else (now + timedelta(days=30))
        days_diff = (due_dt.date() - now.date()).days
        
        if days_diff < 0:
            overdue_count += 1
            overdue_amount += p_bal
        elif days_diff <= 7:
            due_soon_count += 1

    # 3. Cobros y abonos recibidos este mes
    collected_stmt = (
        select(func.coalesce(func.sum(CustomerCreditPayment.amount), Decimal("0")))
        .where(
            CustomerCreditPayment.company_id == c_uuid,
            CustomerCreditPayment.payment_date >= month_start
        )
    )
    collected_res = await db.execute(collected_stmt)
    collected_this_month = float(collected_res.scalar() or 0.0)

    return {
        "total_receivable": total_receivable,
        "total_open_invoices": total_debtors,
        "due_soon_count": due_soon_count,
        "overdue_count": overdue_count,
        "overdue_amount": overdue_amount,
        "collected_this_month": collected_this_month,
    }


# ===== 2. LIST INVOICES / CREDITS (UNIFICADO POR CLIENTE) =====
@router.get("/invoices")
async def list_customer_invoices(
    status: Optional[str] = Query(None),  # 'all', 'open', 'due_soon', 'overdue', 'paid'
    search: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar cuentas por cobrar UNIFICADAS POR CLIENTE.
    La primera venta al crédito marca el inicio del plazo de crédito del cliente.
    """
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_sales_to_credits(db, c_uuid)

    now = datetime.now(timezone.utc)

    # 1. Obtener todos los clientes
    p_stmt = select(Partner).where(
        Partner.company_id == c_uuid,
        Partner.is_deleted == False
    ).order_by(Partner.name.asc())
    if customer_id:
        try:
            p_stmt = p_stmt.where(Partner.id == uuid.UUID(customer_id))
        except ValueError:
            pass

    p_res = await db.execute(p_stmt)
    partners = p_res.scalars().all()

    # 2. Obtener todas las cuentas de crédito vinculadas
    c_stmt = (
        select(CustomerInvoiceCredit)
        .options(selectinload(CustomerInvoiceCredit.payments))
        .where(
            CustomerInvoiceCredit.company_id == c_uuid,
            CustomerInvoiceCredit.is_deleted == False
        )
        .order_by(CustomerInvoiceCredit.issue_date.asc())
    )
    c_res = await db.execute(c_stmt)
    all_credits = c_res.scalars().all()

    credits_by_customer = {}
    for c in all_credits:
        cid = str(c.customer_id) if c.customer_id else None
        if not cid:
            continue
        if cid not in credits_by_customer:
            credits_by_customer[cid] = []
        credits_by_customer[cid].append(c)

    items = []
    for p in partners:
        p_id_str = str(p.id)
        p_credits = credits_by_customer.get(p_id_str, [])
        open_credits = [c for c in p_credits if (c.balance or 0) > 0]

        p_balance = float(p.balance or 0.0)
        credits_balance = sum(float(c.balance or 0.0) for c in open_credits)
        bal = max(p_balance, credits_balance)

        # Si el cliente no tiene deuda y no tiene historial, omitir
        if bal <= 0 and len(p_credits) == 0:
            continue

        tot = sum(float(c.total or 0.0) for c in p_credits) if p_credits else bal
        if tot < bal:
            tot = bal

        # Regla de negocio: la primera venta al crédito marca el inicio del conteo de días
        first_credit_date = None
        if open_credits:
            first_credit_date = open_credits[0].issue_date
        elif p_credits:
            first_credit_date = p_credits[0].issue_date
        else:
            first_credit_date = p.created_at or now

        if first_credit_date and first_credit_date.tzinfo is None:
            first_credit_date = first_credit_date.replace(tzinfo=timezone.utc)

        p_days = p.payment_days or 30
        due_date = (first_credit_date + timedelta(days=p_days)) if first_credit_date else (now + timedelta(days=p_days))

        days_diff = (due_date.date() - now.date()).days
        
        d_status = "paid"
        if bal > 0:
            if days_diff < 0:
                d_status = "overdue"
            elif days_diff <= 7:
                d_status = "due_soon"
            else:
                d_status = "on_time"

        # Filtrar por status
        if status and status != "all":
            if status == "open" and bal <= 0:
                continue
            elif status == "due_soon" and d_status != "due_soon":
                continue
            elif status == "overdue" and d_status != "overdue":
                continue
            elif status == "paid" and d_status != "paid":
                continue

        c_name = p.name
        c_nit = p.nit or "CF"
        c_code = f"CLI-{str(p.id)[:6].upper()}"

        # Número de referencia representativo
        rep_number = open_credits[0].number if open_credits else (p_credits[0].number if p_credits else c_code)

        if search:
            q_low = search.lower()
            matches_inv_num = any(q_low in (c.number or "").lower() for c in p_credits)
            if q_low not in c_name.lower() and q_low not in c_nit.lower() and q_low not in c_code.lower() and not matches_inv_num:
                continue

        rep_id = str(open_credits[0].id) if open_credits else p_id_str

        items.append({
            "id": rep_id,
            "customer_id": p_id_str,
            "number": rep_number,
            "customer_name": c_name,
            "customer_nit": c_nit,
            "customer_phone": p.phone or p.mobile or "",
            "first_credit_date": first_credit_date.strftime("%d/%m/%Y") if first_credit_date else "",
            "issue_date": first_credit_date.strftime("%d/%m/%Y") if first_credit_date else "",
            "due_date": due_date.strftime("%d/%m/%Y") if due_date else "Sin fecha",
            "payment_days": p_days,
            "days_diff": days_diff,
            "total": tot,
            "balance": bal,
            "credit_limit": float(p.credit_limit or 0.0),
            "available_credit": max(0.0, float(p.credit_limit or 0.0) - bal),
            "status": d_status,
            "invoices_count": len(open_credits),
            "notes": f"Crédito unificado de {c_name} (Plazo {p_days} días)",
            "payments_count": sum(len(c.payments or []) for c in p_credits)
        })

    return items


# ===== 3. GET SINGLE CREDIT DETAIL =====
@router.get("/invoices/{invoice_id}")
async def get_customer_invoice_credit(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle unificado de cuenta por cobrar de cliente con sus facturas y abonos."""
    try:
        target_uuid = uuid.UUID(invoice_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido")

    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_sales_to_credits(db, c_uuid)

    # Buscar cliente o factura
    p_stmt = select(Partner).where(Partner.id == target_uuid, Partner.company_id == c_uuid)
    p_res = await db.execute(p_stmt)
    partner = p_res.scalar_one_or_none()

    if not partner:
        i_stmt = select(CustomerInvoiceCredit).where(CustomerInvoiceCredit.id == target_uuid, CustomerInvoiceCredit.company_id == c_uuid)
        i_res = await db.execute(i_stmt)
        inv = i_res.scalar_one_or_none()
        if inv and inv.customer_id:
            p_res = await db.execute(select(Partner).where(Partner.id == inv.customer_id, Partner.company_id == c_uuid))
            partner = p_res.scalar_one_or_none()

    if not partner:
        raise HTTPException(status_code=404, detail="Cliente o cuenta no encontrada")

    # Obtener historial de facturas a crédito y abonos
    c_stmt = (
        select(CustomerInvoiceCredit)
        .options(selectinload(CustomerInvoiceCredit.payments))
        .where(
            CustomerInvoiceCredit.customer_id == partner.id,
            CustomerInvoiceCredit.company_id == c_uuid,
            CustomerInvoiceCredit.is_deleted == False
        )
        .order_by(CustomerInvoiceCredit.issue_date.desc())
    )
    c_res = await db.execute(c_stmt)
    credits = c_res.scalars().all()

    p_stmt = (
        select(CustomerCreditPayment)
        .where(
            CustomerCreditPayment.customer_id == partner.id,
            CustomerCreditPayment.company_id == c_uuid,
            CustomerCreditPayment.is_deleted == False
        )
        .order_by(CustomerCreditPayment.payment_date.desc())
    )
    p_res = await db.execute(p_stmt)
    payments = p_res.scalars().all()

    total_invoiced = sum(float(c.total or 0.0) for c in credits) if credits else float(partner.balance or 0.0)
    open_credits_bal = sum(float(c.balance or 0.0) for c in credits if (c.balance or 0) > 0)
    curr_balance = max(float(partner.balance or 0.0), open_credits_bal)

    if float(partner.balance or 0.0) < open_credits_bal:
        partner.balance = Decimal(str(open_credits_bal))
        await db.commit()
    
    first_dt = credits[-1].issue_date if credits else partner.created_at
    due_dt = (first_dt + timedelta(days=partner.payment_days or 30)) if first_dt else None

    return {
        "id": str(partner.id),
        "number": f"CLI-{str(partner.id)[:6].upper()}",
        "customer_id": str(partner.id),
        "customer_name": partner.name,
        "customer_nit": partner.nit or "CF",
        "customer_phone": partner.phone or partner.mobile or "",
        "issue_date": first_dt.strftime("%d/%m/%Y") if first_dt else "",
        "due_date": due_dt.strftime("%d/%m/%Y") if due_dt else "Sin fecha",
        "payment_days": partner.payment_days or 30,
        "total": max(total_invoiced, curr_balance),
        "balance": curr_balance,
        "credit_limit": float(partner.credit_limit or 0.0),
        "state": "open" if curr_balance > 0 else "paid",
        "notes": f"Crédito unificado de {partner.name}",
        "credits": [
            {
                "id": str(c.id),
                "number": c.number,
                "date": c.issue_date.strftime("%d/%m/%Y") if c.issue_date else "",
                "total": float(c.total or 0.0),
                "balance": float(c.balance or 0.0),
                "notes": c.notes or ""
            }
            for c in credits
        ],
        "payments": [
            {
                "id": str(pmt.id),
                "receipt_number": pmt.receipt_number,
                "amount": float(pmt.amount),
                "date": pmt.payment_date.strftime("%d/%m/%Y %H:%M") if pmt.payment_date else "",
                "method": pmt.payment_method,
                "reference": pmt.reference or "",
                "notes": pmt.notes or ""
            }
            for pmt in payments
        ]
    }


# ===== 4. REGISTER NEW CREDIT ACCOUNT =====
@router.post("/invoices")
async def create_customer_invoice_credit(
    data: CustomerInvoiceCreditCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear manualmente una cuenta de crédito a cliente."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    cust_uuid = None
    if data.customer_id:
        try:
            cust_uuid = uuid.UUID(data.customer_id)
        except ValueError:
            pass

    partner = None
    if cust_uuid:
        p_res = await db.execute(select(Partner).where(Partner.id == cust_uuid, Partner.company_id == c_uuid))
        partner = p_res.scalar_one_or_none()

    if not partner and data.customer_name:
        partner = Partner(
            company_id=c_uuid,
            name=data.customer_name,
            nit=data.customer_nit or "CF",
            credit_limit=Decimal("5000.00"),
            payment_days=data.credit_days or 30,
            partner_type=PartnerType.CUSTOMER
        )
        db.add(partner)
        await db.flush()
        cust_uuid = partner.id

    now = datetime.now(timezone.utc)
    i_date = now
    if data.issue_date:
        try:
            i_date = datetime.strptime(data.issue_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass

    due_date = i_date + timedelta(days=data.credit_days or 30)

    credit_inv = CustomerInvoiceCredit(
        company_id=c_uuid,
        customer_id=cust_uuid,
        number=data.number,
        issue_date=i_date,
        due_date=due_date,
        total=Decimal(str(data.total)),
        balance=Decimal(str(data.total)),
        state="open",
        notes=data.notes
    )
    db.add(credit_inv)

    if partner:
        partner.balance = (partner.balance or Decimal("0")) + Decimal(str(data.total))

    await db.commit()
    await db.refresh(credit_inv)

    return {
        "id": str(credit_inv.id),
        "number": credit_inv.number,
        "total": float(credit_inv.total),
        "balance": float(credit_inv.balance),
        "due_date": credit_inv.due_date.strftime("%d/%m/%Y") if credit_inv.due_date else ""
    }


# ===== 5. REGISTER PAYMENT / ABONO AL CLIENTE =====
@router.post("/invoices/{invoice_id}/payments")
async def register_customer_payment(
    invoice_id: str,
    data: ARPaymentIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Registrar un abono al cliente (aplicando a su saldo y a sus facturas de crédito FIFO)."""
    check_credit_payment_permission(current_user)
    try:
        target_uuid = uuid.UUID(invoice_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido")

    c_uuid = uuid.UUID(str(current_user.company_id))

    # Buscar si es Partner o Invoice
    p_stmt = select(Partner).where(Partner.id == target_uuid, Partner.company_id == c_uuid)
    p_res = await db.execute(p_stmt)
    partner = p_res.scalar_one_or_none()

    inv = None
    if not partner:
        i_stmt = select(CustomerInvoiceCredit).where(CustomerInvoiceCredit.id == target_uuid, CustomerInvoiceCredit.company_id == c_uuid)
        i_res = await db.execute(i_stmt)
        inv = i_res.scalar_one_or_none()
        if inv and inv.customer_id:
            p_res = await db.execute(select(Partner).where(Partner.id == inv.customer_id, Partner.company_id == c_uuid))
            partner = p_res.scalar_one_or_none()

    if not partner and not inv:
        raise HTTPException(status_code=404, detail="Cliente o cuenta por cobrar no encontrada")

    cust_id = partner.id if partner else inv.customer_id
    total_balance = partner.balance if partner else inv.balance

    amt = Decimal(str(data.amount))
    if amt <= 0:
        raise HTTPException(status_code=400, detail="El monto del abono debe ser mayor a 0.")

    if total_balance and amt > total_balance:
        amt = total_balance

    # Correlativo de recibo
    p_count_stmt = select(func.count(CustomerCreditPayment.id)).where(CustomerCreditPayment.company_id == c_uuid)
    p_count_res = await db.execute(p_count_stmt)
    receipt_num = f"REC-{(p_count_res.scalar() or 0) + 1:06d}"

    # Aplicar a las facturas de crédito abiertas de este cliente en orden FIFO
    inv_list_stmt = (
        select(CustomerInvoiceCredit)
        .where(
            CustomerInvoiceCredit.customer_id == cust_id,
            CustomerInvoiceCredit.company_id == c_uuid,
            CustomerInvoiceCredit.balance > 0,
            CustomerInvoiceCredit.is_deleted == False
        )
        .order_by(CustomerInvoiceCredit.issue_date.asc())
    )
    inv_list_res = await db.execute(inv_list_stmt)
    open_invs = inv_list_res.scalars().all()

    remaining_to_apply = amt
    primary_inv_id = None
    for open_inv in open_invs:
        if remaining_to_apply <= 0:
            break
        if not primary_inv_id:
            primary_inv_id = open_inv.id
        to_pay = min(open_inv.balance, remaining_to_apply)
        open_inv.balance = open_inv.balance - to_pay
        if open_inv.balance <= 0:
            open_inv.balance = Decimal("0")
            open_inv.state = "paid"
        remaining_to_apply -= to_pay

    payment = CustomerCreditPayment(
        company_id=c_uuid,
        invoice_id=primary_inv_id or (inv.id if inv else None),
        customer_id=cust_id,
        receipt_number=receipt_num,
        amount=amt,
        payment_date=datetime.now(timezone.utc),
        payment_method=data.payment_method,
        reference=data.reference,
        notes=data.notes
    )
    db.add(payment)

    # Actualizar saldo del partner
    prev_balance = partner.balance if partner else amt
    if partner and partner.balance is not None:
        partner.balance = max(Decimal("0"), partner.balance - amt)

    await db.commit()
    await db.refresh(payment)

    new_bal = float(partner.balance) if partner else 0.0

    return {
        "status": "success",
        "payment_id": str(payment.id),
        "receipt_number": payment.receipt_number,
        "previous_balance": float(prev_balance),
        "paid_amount": float(amt),
        "new_balance": new_bal,
        "message": "¡Abono registrado exitosamente!"
    }


# ===== 6. PAYMENT RECEIPT / COMPROBANTE DE ABONO =====
@router.get("/payments/{payment_id}/receipt")
async def get_payment_receipt(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener datos para recibo de caja / abono imprimible para el cliente."""
    try:
        p_uuid = uuid.UUID(payment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de pago inválido")

    stmt = (
        select(CustomerCreditPayment)
        .options(
            selectinload(CustomerCreditPayment.invoice),
            selectinload(CustomerCreditPayment.customer)
        )
        .where(
            CustomerCreditPayment.id == p_uuid,
            CustomerCreditPayment.company_id == current_user.company_id
        )
    )
    res = await db.execute(stmt)
    pmt = res.scalar_one_or_none()
    if not pmt:
        raise HTTPException(status_code=404, detail="Comprobante de abono no encontrado")

    inv = pmt.invoice
    customer = pmt.customer

    comp_res = await db.execute(select(Company).where(Company.id == current_user.company_id))
    company = comp_res.scalar_one_or_none()

    return {
        "receipt_id": str(pmt.id),
        "receipt_number": pmt.receipt_number,
        "payment_date": pmt.payment_date.strftime("%d/%m/%Y %H:%M") if pmt.payment_date else "",
        "payment_method": pmt.payment_method,
        "reference": pmt.reference or "N/A",
        "notes": pmt.notes or "",
        "amount_paid": float(pmt.amount),
        "invoice": {
            "id": str(inv.id) if inv else "",
            "number": inv.number if inv else "Cobro Directo",
            "total": float(inv.total) if inv else float(pmt.amount),
            "current_balance": float(inv.balance) if inv else 0.0,
            "issue_date": inv.issue_date.strftime("%d/%m/%Y") if inv and inv.issue_date else "",
            "due_date": inv.due_date.strftime("%d/%m/%Y") if inv and inv.due_date else ""
        },
        "customer": {
            "id": str(customer.id) if customer else "",
            "name": customer.name if customer else "Consumidor Final",
            "nit": customer.nit if customer else "CF",
            "phone": customer.phone or customer.mobile if customer else "",
            "email": customer.email if customer else "",
            "address": customer.address if customer else ""
        },
        "company": {
            "name": company.name if company else "POS Empresarial S.A.",
            "nit": company.nit if company else "CF",
            "phone": company.phone if company else "",
            "address": company.address if company else ""
        },
        "user_name": getattr(current_user, 'full_name', '') or getattr(current_user, 'username', '') or getattr(current_user, 'email', 'Administrador')
    }


# ===== 7. AGING REPORT (ANTIGÜEDAD DE SALDOS) =====
@router.get("/aging")
async def get_ar_aging_report(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Reporte ejecutivo de Antigüedad de Saldos por Cliente (0-30, 31-60, 61-90, 90+)."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_sales_to_credits(db, c_uuid)
    now = datetime.now(timezone.utc)

    stmt = (
        select(CustomerInvoiceCredit)
        .options(selectinload(CustomerInvoiceCredit.customer))
        .where(
            CustomerInvoiceCredit.company_id == c_uuid,
            CustomerInvoiceCredit.balance > 0,
            CustomerInvoiceCredit.is_deleted == False
        )
    )
    res = await db.execute(stmt)
    invoices = res.scalars().all()

    summary = {
        "current": 0.0,
        "range_1_30": 0.0,
        "range_31_60": 0.0,
        "range_61_90": 0.0,
        "range_90_plus": 0.0,
        "total": 0.0
    }

    customers_dict = {}

    for inv in invoices:
        bal = float(inv.balance or 0.0)
        due_dt = inv.due_date
        if due_dt:
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            days_overdue = (now.date() - due_dt.date()).days
        else:
            days_overdue = 0

        cust_id = str(inv.customer_id) if inv.customer_id else "unknown"
        cust_name = inv.customer.name if inv.customer else "Cliente No Identificado"
        cust_nit = inv.customer.nit if inv.customer else "CF"
        cust_limit = float(inv.customer.credit_limit or 0.0) if inv.customer else 0.0

        if cust_id not in customers_dict:
            customers_dict[cust_id] = {
                "customer_id": cust_id,
                "customer_name": cust_name,
                "customer_nit": cust_nit,
                "credit_limit": cust_limit,
                "current": 0.0,
                "range_1_30": 0.0,
                "range_31_60": 0.0,
                "range_61_90": 0.0,
                "range_90_plus": 0.0,
                "total": 0.0,
                "invoices_count": 0
            }

        target = customers_dict[cust_id]
        target["invoices_count"] += 1
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

    customer_list = list(customers_dict.values())
    customer_list.sort(key=lambda x: x["total"], reverse=True)

    return {
        "summary": summary,
        "customers": customer_list
    }


# ===== 8. CUSTOMER STATEMENT (ESTADO DE CUENTA 360) =====
@router.get("/customers/{customer_id}/statement")
async def get_customer_account_statement(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Estado de cuenta individual de un cliente con saldo, límite y movimientos."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_sales_to_credits(db, c_uuid)

    try:
        cust_uuid = uuid.UUID(customer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cliente inválido")

    c_stmt = select(Partner).where(
        Partner.id == cust_uuid,
        Partner.company_id == c_uuid
    )
    c_res = await db.execute(c_stmt)
    customer = c_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    # Invoices
    inv_stmt = (
        select(CustomerInvoiceCredit)
        .options(selectinload(CustomerInvoiceCredit.payments))
        .where(
            CustomerInvoiceCredit.customer_id == cust_uuid,
            CustomerInvoiceCredit.company_id == current_user.company_id,
            CustomerInvoiceCredit.is_deleted == False
        )
        .order_by(CustomerInvoiceCredit.issue_date.desc())
    )
    inv_res = await db.execute(inv_stmt)
    invoices = inv_res.scalars().all()

    total_debt = sum(float(i.balance or 0) for i in invoices)
    limit = float(customer.credit_limit or 0.0)
    available_credit = max(0.0, limit - total_debt)

    # Payments
    pay_stmt = (
        select(CustomerCreditPayment)
        .where(
            CustomerCreditPayment.customer_id == cust_uuid,
            CustomerCreditPayment.company_id == current_user.company_id
        )
        .order_by(CustomerCreditPayment.payment_date.desc())
    )
    pay_res = await db.execute(pay_stmt)
    payments = pay_res.scalars().all()

    return {
        "customer": {
            "id": str(customer.id),
            "name": customer.name,
            "nit": customer.nit,
            "phone": customer.phone or customer.mobile or "",
            "email": customer.email or "",
            "credit_limit": limit,
            "payment_days": customer.payment_days or 30,
            "total_debt": total_debt,
            "available_credit": available_credit,
            "credit_status": str(customer.credit_status.value if hasattr(customer.credit_status, 'value') else customer.credit_status)
        },
        "invoices": [
            {
                "id": str(i.id),
                "number": i.number,
                "issue_date": i.issue_date.strftime("%d/%m/%Y") if i.issue_date else "",
                "due_date": i.due_date.strftime("%d/%m/%Y") if i.due_date else "",
                "total": float(i.total or 0),
                "balance": float(i.balance or 0),
                "state": i.state
            }
            for i in invoices
        ],
        "payments": [
            {
                "id": str(p.id),
                "receipt_number": p.receipt_number,
                "amount": float(p.amount),
                "payment_date": p.payment_date.strftime("%d/%m/%Y %H:%M") if p.payment_date else "",
                "method": p.payment_method,
                "reference": p.reference or ""
            }
            for p in payments
        ]
    }


# ===== 9. EXPORT CSV =====
@router.get("/export")
async def export_ar_csv(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Descargar estado general de Cuentas por Cobrar en formato CSV."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_sales_to_credits(db, c_uuid)
    now = datetime.now(timezone.utc)

    stmt = (
        select(CustomerInvoiceCredit)
        .options(selectinload(CustomerInvoiceCredit.customer))
        .where(
            CustomerInvoiceCredit.company_id == c_uuid,
            CustomerInvoiceCredit.is_deleted == False
        )
        .order_by(CustomerInvoiceCredit.due_date.asc())
    )
    res = await db.execute(stmt)
    credits = res.scalars().all()

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)

    writer.writerow([
        "Cliente",
        "NIT",
        "No. Factura / Credito",
        "Fecha Emision",
        "Fecha Vencimiento",
        "Dias Mora / Restantes",
        "Monto Total (Q)",
        "Saldo Pendiente (Q)",
        "Estado",
        "Notas"
    ])

    for c in credits:
        bal = float(c.balance or 0.0)
        due_dt = c.due_date
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
            c.customer.name if c.customer else "N/A",
            c.customer.nit if c.customer else "CF",
            c.number,
            c.issue_date.strftime("%d/%m/%Y") if c.issue_date else "",
            c.due_date.strftime("%d/%m/%Y") if c.due_date else "",
            days_diff,
            f"{float(c.total or 0.0):.2f}",
            f"{bal:.2f}",
            status_txt,
            c.notes or ""
        ])

    csv_data = output.getvalue()
    filename = f"cuentas_por_cobrar_{now.strftime('%Y%m%d_%H%M')}.csv"

    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ===== 10. CUSTOMERS LOOKUP FOR AUTOCOMPLETE =====
@router.get("/customers-lookup")
async def lookup_ar_customers(
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Búsqueda rápida de clientes para autocompletado en CxC y abonos."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _sync_sales_to_credits(db, c_uuid)

    stmt = select(Partner).where(
        Partner.company_id == c_uuid,
        Partner.is_deleted == False
    ).order_by(Partner.name.asc()).limit(50)

    if q:
        stmt = stmt.where(
            (Partner.name.ilike(f"%{q}%")) | (Partner.nit.ilike(f"%{q}%"))
        )

    res = await db.execute(stmt)
    partners = res.scalars().all()

    items = []
    for p in partners:
        limit = float(p.credit_limit or 0.0)
        bal = float(p.balance or 0.0)
        items.append({
            "id": str(p.id),
            "name": p.name,
            "nit": p.nit or "CF",
            "phone": p.phone or p.mobile or "",
            "email": p.email or "",
            "credit_limit": limit,
            "balance": bal,
            "available_credit": max(0.0, limit - bal),
            "payment_days": p.payment_days or 30,
            "credit_status": str(p.credit_status.value if hasattr(p.credit_status, 'value') else p.credit_status)
        })
    return {"items": items}


# ===== 11. EXPORT CUSTOMER STATEMENT CSV =====
@router.get("/customers/{customer_id}/export")
async def export_customer_statement_csv(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Descargar estado de cuenta individual de un cliente en formato CSV."""
    cust_uuid = uuid.UUID(customer_id)
    c_stmt = select(Partner).where(
        Partner.id == cust_uuid,
        Partner.company_id == current_user.company_id
    )
    c_res = await db.execute(c_stmt)
    customer = c_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    inv_stmt = select(CustomerInvoiceCredit).where(
        CustomerInvoiceCredit.customer_id == cust_uuid,
        CustomerInvoiceCredit.company_id == current_user.company_id,
        CustomerInvoiceCredit.is_deleted == False
    ).order_by(CustomerInvoiceCredit.issue_date.asc())
    inv_res = await db.execute(inv_stmt)
    invoices = inv_res.scalars().all()

    pay_stmt = select(CustomerCreditPayment).where(
        CustomerCreditPayment.customer_id == cust_uuid,
        CustomerCreditPayment.company_id == current_user.company_id
    ).order_by(CustomerCreditPayment.payment_date.asc())
    pay_res = await db.execute(pay_stmt)
    payments = pay_res.scalars().all()

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)

    writer.writerow(["ESTADO DE CUENTA DE CLIENTE - POS EMPRESARIAL"])
    writer.writerow(["Cliente", customer.name, "NIT", customer.nit])
    writer.writerow(["Limite de Credito (Q)", f"{float(customer.credit_limit or 0):.2f}", "Dias de Credito", customer.payment_days or 30])
    writer.writerow([])
    writer.writerow(["FACTURAS Y CARGOS A CREDITO"])
    writer.writerow(["No. Documento", "Fecha Emision", "Fecha Vencimiento", "Total Facturado (Q)", "Saldo Pendiente (Q)", "Estado"])
    for i in invoices:
        writer.writerow([
            i.number,
            i.issue_date.strftime("%d/%m/%Y") if i.issue_date else "",
            i.due_date.strftime("%d/%m/%Y") if i.due_date else "",
            f"{float(i.total or 0):.2f}",
            f"{float(i.balance or 0):.2f}",
            i.state.upper()
        ])

    writer.writerow([])
    writer.writerow(["HISTORIAL DE ABONOS Y PAGOS RECIBIDOS"])
    writer.writerow(["No. Recibo", "Fecha Abono", "Metodo de Pago", "Referencia", "Monto Abonado (Q)"])
    for p in payments:
        writer.writerow([
            p.receipt_number,
            p.payment_date.strftime("%d/%m/%Y %H:%M") if p.payment_date else "",
            p.payment_method.upper(),
            p.reference or "N/A",
            f"{float(p.amount or 0):.2f}"
        ])

    csv_data = output.getvalue()
    clean_name = "".join(c for c in customer.name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    filename = f"estado_cuenta_{clean_name}_{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
