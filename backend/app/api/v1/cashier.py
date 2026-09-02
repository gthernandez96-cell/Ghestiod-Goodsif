"""API v1 — Control de Caja y Sesiones de Cajero."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List, Dict
from decimal import Decimal
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.cashier import CashierSession, SessionState, CashMovement, CashMovementType, PaymentMethod
from app.models.sale import SaleOrder, SalePayment, SaleState

router = APIRouter(prefix="/cashier")


# ===== SCHEMAS =====
class OpenSessionIn(BaseModel):
    branch_id: str
    pos_id: Optional[str] = None
    opening_amount: Decimal = Decimal("0")
    denominations: Optional[Dict[str, int]] = None
    notes: Optional[str] = None


class CloseSessionIn(BaseModel):
    closing_amount_real: Decimal
    denominations: Optional[Dict[str, int]] = None
    notes: Optional[str] = None


class CashMovementIn(BaseModel):
    session_id: str
    move_type: str = "cash_in"  # "cash_in" or "cash_out"
    amount: Decimal
    reason: str
    payment_method: str = "cash"


def _generate_session_number() -> str:
    now = datetime.now()
    seq = str(uuid.uuid4().int)[:6]
    return f"SESS-{now.strftime('%Y%m%d')}-{seq}"


# ===== ENDPOINTS =====
@router.get("/sessions/active")
async def get_active_session(
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener la sesión de caja activa del usuario actual con desglose de ventas por forma de pago."""
    from app.models.sale import SaleOrder, SalePayment, SaleState

    stmt = select(CashierSession).where(
        CashierSession.user_id == current_user.id,
        CashierSession.state == SessionState.OPEN
    )

    if branch_id:
        try:
            stmt = stmt.where(CashierSession.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    stmt = stmt.order_by(CashierSession.opened_at.desc()).limit(1)
    res = await db.execute(stmt)
    session = res.scalar_one_or_none()

    if not session:
        return {"has_active_session": False, "session": None}

    opening = float(session.opening_balance or 0)
    cash_in = float(session.total_cash_in or 0)
    cash_out = float(session.total_cash_out or 0)
    returns = float(session.total_returns or 0)

    # Consultar ventas vinculadas a esta sesión
    pay_stmt = (
        select(
            SalePayment.payment_method,
            func.sum(SalePayment.amount)
        )
        .join(SaleOrder, SalePayment.sale_id == SaleOrder.id)
        .where(
            SaleOrder.cashier_session_id == session.id,
            SaleOrder.state != SaleState.CANCELLED
        )
        .group_by(SalePayment.payment_method)
    )
    pay_res = await db.execute(pay_stmt)
    pay_map = {m: float(a or 0) for m, a in pay_res.all()}

    # Conteo de órdenes y total general
    count_stmt = select(
        func.count(SaleOrder.id),
        func.sum(SaleOrder.total)
    ).where(
        SaleOrder.cashier_session_id == session.id,
        SaleOrder.state != SaleState.CANCELLED
    )
    c_res = await db.execute(count_stmt)
    c_row = c_res.one_or_none()
    sales_count = int(c_row[0]) if c_row and c_row[0] else 0
    sales_total = float(c_row[1] or 0) if c_row and c_row[1] else 0.0

    # Total efectivo proveniente de ventas ÚNICAMENTE por pago cash o CashMovement
    cash_sales = pay_map.get("cash", 0.0)

    move_stmt = select(func.sum(CashMovement.amount)).where(
        CashMovement.session_id == session.id,
        CashMovement.move_type == CashMovementType.SALE
    )
    move_res = await db.execute(move_stmt)
    move_cash_val = float(move_res.scalar() or 0.0)
    if move_cash_val > cash_sales:
        cash_sales = move_cash_val

    card_sales = pay_map.get("card", 0.0) + pay_map.get("transfer", 0.0)
    credit_sales = pay_map.get("credit", 0.0)

    # Verificar si existen saldos pendientes por ventas al crédito
    due_stmt = select(func.sum(SaleOrder.amount_due)).where(
        SaleOrder.cashier_session_id == session.id,
        SaleOrder.state != SaleState.CANCELLED
    )
    due_res = await db.execute(due_stmt)
    due_val = float(due_res.scalar() or 0.0)
    if due_val > credit_sales:
        credit_sales = due_val

    if sales_total == 0.0:
        sales_total = cash_sales + card_sales + credit_sales

    # Saldo esperado en efectivo en gaveta (ESTRICTAMENTE EFECTIVO)
    expected = opening + cash_in + cash_sales - cash_out - returns

    return {
        "has_active_session": True,
        "session": {
            "id": str(session.id),
            "session_number": session.session_number,
            "branch_id": str(session.branch_id),
            "opened_at": session.opened_at.isoformat() if session.opened_at else None,
            "opening_balance": opening,
            "opening_amount": opening,
            "total_sales": sales_total,
            "total_cash_sales": cash_sales,
            "total_credit_sales": credit_sales,
            "total_card_sales": card_sales,
            "total_transfer_sales": pay_map.get("transfer", 0.0),
            "sales_count": sales_count,
            "total_cash_in": cash_in,
            "total_cash_out": cash_out,
            "expected_cash": expected,
            "state": session.state.value if hasattr(session.state, 'value') else str(session.state),
        }
    }


@router.get("/sessions/")
async def list_sessions(
    branch_id: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar historial de sesiones de caja."""
    stmt = (
        select(CashierSession)
        .options(selectinload(CashierSession.user), selectinload(CashierSession.branch))
        .order_by(CashierSession.opened_at.desc())
        .limit(limit)
    )

    if branch_id:
        try:
            stmt = stmt.where(CashierSession.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    sessions = res.scalars().all()

    return [
        {
            "id": str(s.id),
            "session_number": s.session_number,
            "user_name": s.user.full_name if s.user else "N/A",
            "branch_name": s.branch.name if s.branch else "N/A",
            "state": s.state.value if hasattr(s.state, 'value') else str(s.state),
            "opened_at": s.opened_at.isoformat() if s.opened_at else None,
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "opening_balance": float(s.opening_balance or 0),
            "closing_balance_real": float(s.closing_balance_real or 0),
            "closing_difference": float(s.closing_difference or 0),
            "total_sales": float(s.total_sales or 0),
        }
        for s in sessions
    ]


@router.post("/open")
@router.post("/sessions/")
async def open_session(
    data: OpenSessionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Apertura de una nueva sesión de caja."""
    try:
        b_uuid = uuid.UUID(data.branch_id)
    except Exception:
        # Fallback to first available branch if branch_id string is invalid
        from app.models.company import Branch
        b_res = await db.execute(select(Branch.id).limit(1))
        b_uuid = b_res.scalar_one_or_none()
        if not b_uuid:
            raise HTTPException(status_code=400, detail="Sucursal no encontrada")

    # Verificar si ya tiene sesión abierta
    active_stmt = select(CashierSession).where(
        CashierSession.user_id == current_user.id,
        CashierSession.state == SessionState.OPEN,
        CashierSession.branch_id == b_uuid
    )
    active_res = await db.execute(active_stmt)
    existing_sess = active_res.scalar_one_or_none()
    if existing_sess:
        return {
            "id": str(existing_sess.id),
            "number": existing_sess.session_number,
            "opening_balance": float(existing_sess.opening_balance or 0),
            "status": "opened",
            "message": "Sesión ya estaba abierta"
        }

    session = CashierSession(
        branch_id=b_uuid,
        user_id=current_user.id,
        pos_id=uuid.UUID(data.pos_id) if (data.pos_id and len(data.pos_id) == 36) else None,
        session_number=_generate_session_number(),
        state=SessionState.OPEN,
        opened_at=datetime.now(timezone.utc),
        opening_balance=data.opening_amount,
        opening_notes=data.notes,
        opening_denominations=data.denominations or {},
    )
    db.add(session)
    await db.flush()

    # Registrar movimiento inicial de apertura
    initial_move = CashMovement(
        session_id=session.id,
        move_type=CashMovementType.OPEN_BALANCE,
        payment_method=PaymentMethod.CASH,
        amount=data.opening_amount,
        reference="Fondo de Apertura",
        notes=data.notes,
        user_id=current_user.id,
    )
    db.add(initial_move)
    await db.commit()
    await db.refresh(session)

    return {
        "id": str(session.id),
        "number": session.session_number,
        "opening_balance": float(session.opening_balance),
        "status": "opened",
    }


@router.post("/sessions/{session_id}/close")
async def close_session(
    session_id: str,
    data: CloseSessionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cierre de sesión de caja y arqueo."""
    try:
        s_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de sesión inválido")

    stmt = select(CashierSession).where(CashierSession.id == s_uuid)
    res = await db.execute(stmt)
    session = res.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    if session.state == SessionState.CLOSED:
        raise HTTPException(status_code=400, detail="La sesión ya fue cerrada")

    # Consultar ventas vinculadas a esta sesión por método de pago
    pay_stmt = (
        select(
            SalePayment.payment_method,
            func.sum(SalePayment.amount)
        )
        .join(SaleOrder, SalePayment.sale_id == SaleOrder.id)
        .where(
            SaleOrder.cashier_session_id == session.id,
            SaleOrder.state != SaleState.CANCELLED
        )
        .group_by(SalePayment.payment_method)
    )
    pay_res = await db.execute(pay_stmt)
    pay_map = {m: float(a or 0) for m, a in pay_res.all()}

    cash_sales = Decimal(str(pay_map.get("cash", 0.0)))
    move_stmt = select(func.sum(CashMovement.amount)).where(
        CashMovement.session_id == session.id,
        CashMovement.move_type == CashMovementType.SALE
    )
    move_res = await db.execute(move_stmt)
    move_cash_val = Decimal(str(move_res.scalar() or "0.0"))
    if move_cash_val > cash_sales:
        cash_sales = move_cash_val

    # Calcular saldo esperado = saldo inicial + entradas + ventas efectivo - salidas - devoluciones
    expected = (
        (session.opening_balance or Decimal("0"))
        + (session.total_cash_in or Decimal("0"))
        + cash_sales
        - (session.total_cash_out or Decimal("0"))
        - (session.total_returns or Decimal("0"))
    )

    difference = data.closing_amount_real - expected

    session.state = SessionState.CLOSED
    session.closed_at = datetime.now(timezone.utc)
    session.closing_balance_expected = expected
    session.closing_balance_real = data.closing_amount_real
    session.closing_difference = difference
    session.closing_denominations = data.denominations or {}
    session.closing_notes = data.notes

    # Registrar movimiento final
    close_move = CashMovement(
        session_id=session.id,
        move_type=CashMovementType.CLOSE_BALANCE,
        payment_method=PaymentMethod.CASH,
        amount=data.closing_amount_real,
        reference="Arqueo de Cierre",
        notes=data.notes,
        user_id=current_user.id,
    )
    db.add(close_move)
    await db.commit()

    return {
        "id": str(session.id),
        "session_number": session.session_number,
        "closing_balance_expected": float(expected),
        "closing_balance_real": float(data.closing_amount_real),
        "difference": float(difference),
        "closing_difference": float(difference),
        "status": "closed",
        "z_report_url": f"/cashier/sessions/{session.id}/z-report"
    }


@router.post("/movements/")
async def add_cash_movement(
    data: CashMovementIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Registrar un ingreso o retiro de efectivo en caja."""
    try:
        s_uuid = uuid.UUID(data.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de sesión inválido")

    stmt = select(CashierSession).where(CashierSession.id == s_uuid)
    res = await db.execute(stmt)
    session = res.scalar_one_or_none()

    if not session or session.state != SessionState.OPEN:
        raise HTTPException(status_code=400, detail="Sesión no encontrada o no está abierta")

    m_type = CashMovementType.CASH_IN if data.move_type.lower() == "cash_in" else CashMovementType.CASH_OUT

    move = CashMovement(
        session_id=session.id,
        move_type=m_type,
        payment_method=PaymentMethod.CASH,
        amount=data.amount,
        reference=data.reason,
        user_id=current_user.id,
    )
    db.add(move)

    if m_type == CashMovementType.CASH_IN:
        session.total_cash_in = (session.total_cash_in or Decimal("0")) + data.amount
    else:
        session.total_cash_out = (session.total_cash_out or Decimal("0")) + data.amount

    await db.commit()
    return {"id": str(move.id), "status": "ok", "move_type": m_type.value, "amount": float(data.amount)}


@router.get("/terminals")
async def list_terminals_status(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar el estado en vivo de los Puntos de Venta (Terminales / Cajas) para el Dashboard Odoo 17."""
    from app.models.company import Branch, Warehouse
    from app.models.sale import SaleOrder

    # Obtener sucursales
    b_stmt = select(Branch).where(Branch.company_id == current_user.company_id, Branch.is_deleted == False)
    b_res = await db.execute(b_stmt)
    branches = b_res.scalars().all()

    # Obtener sesiones abiertas
    open_sess_stmt = (
        select(CashierSession)
        .options(selectinload(CashierSession.user), selectinload(CashierSession.branch))
        .where(
            CashierSession.state == SessionState.OPEN
        )
    )
    sess_res = await db.execute(open_sess_stmt)
    open_sessions = sess_res.scalars().all()
    open_sess_by_branch = {s.branch_id: s for s in open_sessions}

    terminals = []
    for idx, b in enumerate(branches, start=1):
        active_s = open_sess_by_branch.get(b.id)
        if active_s:
            opening = float(active_s.opening_balance or 0)
            sales = float(active_s.total_sales or 0)
            cash_in = float(active_s.total_cash_in or 0)
            cash_out = float(active_s.total_cash_out or 0)
            expected = opening + cash_in + sales - cash_out

            terminals.append({
                "id": str(active_s.id),
                "terminal_name": f"Caja {idx:02d} — {b.name}",
                "branch_id": str(b.id),
                "branch_name": b.name,
                "is_open": True,
                "status_label": "En Sesión (Abierta)",
                "cashier_name": active_s.user.full_name if active_s.user else "Cajero Activo",
                "opened_at": active_s.opened_at.strftime("%d/%m/%Y %H:%M") if active_s.opened_at else "Hoy",
                "session_number": active_s.session_number,
                "opening_balance": opening,
                "total_sales": sales,
                "expected_cash": expected,
            })
        else:
            terminals.append({
                "id": None,
                "terminal_name": f"Caja {idx:02d} — {b.name}",
                "branch_id": str(b.id),
                "branch_name": b.name,
                "is_open": False,
                "status_label": "Cerrada",
                "cashier_name": "Sin sesión activa",
                "opened_at": None,
                "session_number": None,
                "opening_balance": 0.0,
                "total_sales": 0.0,
                "expected_cash": 0.0,
            })

    return terminals


@router.get("/sessions/{session_id}/movements")
async def list_session_movements(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar entradas y salidas de efectivo registradas en una sesión."""
    try:
        s_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de sesión inválido")

    stmt = select(CashMovement).where(CashMovement.session_id == s_uuid).order_by(CashMovement.created_at.desc())
    res = await db.execute(stmt)
    moves = res.scalars().all()

    return [
        {
            "id": str(m.id),
            "move_type": m.move_type.value if hasattr(m.move_type, "value") else str(m.move_type),
            "amount": float(m.amount or 0),
            "reason": m.reference or "Movimiento de caja",
            "created_at": m.created_at.strftime("%H:%M:%S") if m.created_at else ""
        }
        for m in moves
    ]


@router.get("/sessions/{session_id}/orders")
async def list_session_orders(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar ventas / tickets emitidos en una sesión."""
    from app.models.sale import SaleOrder
    try:
        s_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de sesión inválido")

    stmt = select(SaleOrder).where(SaleOrder.cashier_session_id == s_uuid).order_by(SaleOrder.created_at.desc())
    res = await db.execute(stmt)
    orders = res.scalars().all()

    return [
        {
            "id": str(o.id),
            "number": o.number,
            "customer_name": o.customer_name or "Consumidor Final",
            "customer_nit": o.customer_nit or "CF",
            "total": float(o.total or 0),
            "time": o.sale_date.strftime("%H:%M:%S") if o.sale_date else o.created_at.strftime("%H:%M:%S"),
            "fel_uuid": o.fel_uuid,
            "fel_number": o.fel_number
        }
        for o in orders
    ]

