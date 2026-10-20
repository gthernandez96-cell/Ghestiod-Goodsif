"""API v1 — Control de Caja y Sesiones de Cajero."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List, Dict
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.cashier import CashierSession, SessionState, CashMovement, CashMovementType, PaymentMethod
from app.models.sale import SaleOrder, SalePayment, SaleState

router = APIRouter(prefix="/cashier")

try:
    from zoneinfo import ZoneInfo
    GUATEMALA_TZ = ZoneInfo("America/Guatemala")
except Exception:
    GUATEMALA_TZ = timezone(timedelta(hours=-6))


def to_local_dt(dt: Optional[datetime], tz=GUATEMALA_TZ) -> Optional[datetime]:
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
async def compute_session_financials(session: CashierSession, db: AsyncSession) -> dict:
    """
    Calcula con precisión contable el cuadre de caja de una sesión:
    - Desglosa ventas por método de pago asegurando que el efectivo de una venta
      nunca exceda el total de la orden (el cambio/vuelto no queda en la gaveta).
    - Saldo Esperado = Fondo Apertura + Entradas Efectivo + Ventas al Contado (Efectivo) - Salidas - Devoluciones.
    """
    from app.models.sale import SaleOrder, SalePayment, SaleState
    from sqlalchemy.orm import selectinload

    opening = float(session.opening_balance or 0.0)
    cash_in = float(session.total_cash_in or 0.0)
    cash_out = float(session.total_cash_out or 0.0)
    returns = float(session.total_returns or 0.0)

    # Validar movimientos de caja por si no actualizaron los acumuladores del modelo
    m_in_stmt = select(func.sum(CashMovement.amount)).where(
        CashMovement.session_id == session.id,
        CashMovement.move_type == CashMovementType.CASH_IN
    )
    m_in_val = float((await db.execute(m_in_stmt)).scalar() or 0.0)
    if m_in_val > cash_in:
        cash_in = m_in_val

    m_out_stmt = select(func.sum(CashMovement.amount)).where(
        CashMovement.session_id == session.id,
        CashMovement.move_type == CashMovementType.CASH_OUT
    )
    m_out_val = float((await db.execute(m_out_stmt)).scalar() or 0.0)
    if m_out_val > cash_out:
        cash_out = m_out_val

    # Cargar órdenes vinculadas a la sesión con sus pagos
    sales_stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.payments))
        .where(
            SaleOrder.cashier_session_id == session.id,
            SaleOrder.state != SaleState.CANCELLED
        )
    )
    sales_res = await db.execute(sales_stmt)
    orders = sales_res.scalars().all()

    sales_count = len(orders)
    sales_total = 0.0
    cash_sales = 0.0
    card_sales = 0.0
    transfer_sales = 0.0
    credit_sales = 0.0

    for ord in orders:
        ord_total = float(ord.total or 0.0)
        sales_total += ord_total

        o_cash = 0.0
        o_card = 0.0
        o_transfer = 0.0
        o_credit = 0.0

        for p in (ord.payments or []):
            pm = str(getattr(p.payment_method, "value", p.payment_method) or "").lower()
            amt = float(p.amount or 0.0)
            if pm == "cash":
                o_cash += amt
            elif pm in ("card", "tarjeta"):
                o_card += amt
            elif pm in ("transfer", "transferencia", "qr"):
                o_transfer += amt
            elif pm in ("credit", "credito", "crédito"):
                o_credit += amt

        # Lo que no es efectivo cubre parte de la orden
        non_cash = o_card + o_transfer + o_credit
        cash_needed = max(0.0, ord_total - non_cash)
        # El efectivo real que entra a la gaveta por esta orden se topa estrictamente a lo facturado en efectivo
        net_cash = min(o_cash, cash_needed) if o_cash > 0 else 0.0

        cash_sales += net_cash
        card_sales += o_card
        transfer_sales += o_transfer

        if o_credit > 0:
            credit_sales += o_credit
        elif ord.amount_due and float(ord.amount_due) > 0 and o_cash == 0 and non_cash == 0:
            credit_sales += float(ord.amount_due)

    # Si no hay órdenes vinculadas pero hay movimientos de venta registrados directamente
    if sales_count == 0:
        move_stmt = select(func.sum(CashMovement.amount)).where(
            CashMovement.session_id == session.id,
            CashMovement.move_type == CashMovementType.SALE
        )
        move_res = await db.execute(move_stmt)
        move_val = float(move_res.scalar() or 0.0)
        if move_val > 0:
            cash_sales = move_val
            sales_total = move_val
            sales_count = 1

    expected_cash = opening + cash_in + cash_sales - cash_out - returns

    return {
        "opening": opening,
        "cash_in": cash_in,
        "cash_out": cash_out,
        "returns": returns,
        "sales_count": sales_count,
        "sales_total": round(sales_total, 2),
        "cash_sales": round(cash_sales, 2),
        "card_sales": round(card_sales, 2),
        "transfer_sales": round(transfer_sales, 2),
        "credit_sales": round(credit_sales, 2),
        "expected_cash": round(expected_cash, 2),
    }


@router.get("/sessions/active")
async def get_active_session(
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener la sesión de caja activa del usuario actual con desglose de ventas por forma de pago."""
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

    fin = await compute_session_financials(session, db)

    return {
        "has_active_session": True,
        "session": {
            "id": str(session.id),
            "session_number": session.session_number,
            "branch_id": str(session.branch_id),
            "opened_at": session.opened_at.isoformat() if session.opened_at else None,
            "opening_balance": fin["opening"],
            "opening_amount": fin["opening"],
            "total_sales": fin["sales_total"],
            "total_cash_sales": fin["cash_sales"],
            "total_credit_sales": fin["credit_sales"],
            "total_card_sales": fin["card_sales"],
            "total_transfer_sales": fin["transfer_sales"],
            "sales_count": fin["sales_count"],
            "total_cash_in": fin["cash_in"],
            "total_cash_out": fin["cash_out"],
            "expected_cash": fin["expected_cash"],
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

    # Si el usuario es cajero, forzar a su caja / sucursal asignada
    role_codes = [r.code for r in current_user.roles] if hasattr(current_user, "roles") and current_user.roles else []
    is_admin_or_manager = bool(
        getattr(current_user, "is_superuser", False)
        or getattr(current_user, "is_admin", False)
        or "admin" in role_codes
        or "store_manager" in role_codes
        or getattr(current_user, "username", "") == "admin"
        or (getattr(current_user, "email", "") and "admin" in getattr(current_user, "email", ""))
    )
    if not is_admin_or_manager:
        allowed_branch_ids = set()
        if current_user.current_branch_id:
            allowed_branch_ids.add(current_user.current_branch_id)
        if hasattr(current_user, "branches") and current_user.branches:
            for ub in current_user.branches:
                allowed_branch_ids.add(ub.id)
        if allowed_branch_ids and b_uuid not in allowed_branch_ids:
            b_uuid = next(iter(allowed_branch_ids))

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

    # Calcular cuadre financiero exacto
    fin = await compute_session_financials(session, db)
    expected = Decimal(str(fin["expected_cash"]))
    difference = data.closing_amount_real - expected

    session.state = SessionState.CLOSED
    session.closed_at = datetime.now(timezone.utc)
    session.closing_balance_expected = expected
    session.closing_balance_real = data.closing_amount_real
    session.closing_difference = difference
    session.closing_denominations = data.denominations or {}
    session.closing_notes = data.notes
    session.total_sales = Decimal(str(fin["sales_total"]))

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
    """Listar el estado en vivo de los Puntos de Venta (Terminales / Cajas) para el Dashboard Odoo 17.
    Los usuarios con rol Cajero únicamente pueden ver la caja/sucursal que tienen asignada.
    """
    from app.models.company import Branch, Warehouse
    from app.models.sale import SaleOrder

    # Determinar si el usuario tiene privilegios administrativos o de supervisión
    role_codes = [r.code for r in current_user.roles] if hasattr(current_user, "roles") and current_user.roles else []
    is_admin_or_manager = bool(
        getattr(current_user, "is_superuser", False)
        or getattr(current_user, "is_admin", False)
        or "admin" in role_codes
        or "store_manager" in role_codes
        or "accountant" in role_codes
        or getattr(current_user, "username", "") == "admin"
        or (getattr(current_user, "email", "") and "admin" in getattr(current_user, "email", ""))
    )

    # Obtener todas las sucursales de la empresa ordenadas cronológicamente
    b_stmt = select(Branch).where(Branch.company_id == current_user.company_id, Branch.is_deleted == False).order_by(Branch.created_at.asc())
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

    # Si es cajero, determinar su sucursal / caja asignada
    allowed_branch_ids = set()
    if not is_admin_or_manager:
        if current_user.current_branch_id:
            allowed_branch_ids.add(current_user.current_branch_id)
        if hasattr(current_user, "branches") and current_user.branches:
            for ub in current_user.branches:
                allowed_branch_ids.add(ub.id)

        # Si el cajero ya tiene una sesión abierta activa
        for s in open_sessions:
            if s.user_id == current_user.id:
                allowed_branch_ids.add(s.branch_id)

        # Si aún no tiene sucursal asignada en perfil, asignar por defecto la sucursal principal o la primera
        if not allowed_branch_ids and branches:
            main_b = next((b for b in branches if getattr(b, "is_main", False)), branches[0])
            allowed_branch_ids.add(main_b.id)

    # Mapeo de sesiones abiertas por sucursal (para cajero, priorizar su propia sesión)
    open_sess_by_branch = {}
    for s in open_sessions:
        if not is_admin_or_manager:
            if s.user_id == current_user.id:
                open_sess_by_branch[s.branch_id] = s
            elif s.branch_id not in open_sess_by_branch:
                open_sess_by_branch[s.branch_id] = s
        else:
            open_sess_by_branch[s.branch_id] = s

    terminals = []
    for idx, b in enumerate(branches, start=1):
        # Filtrar si es cajero y la sucursal no es la asignada
        if not is_admin_or_manager and b.id not in allowed_branch_ids:
            continue

        active_s = open_sess_by_branch.get(b.id)
        if active_s:
            fin = await compute_session_financials(active_s, db)

            terminals.append({
                "id": str(active_s.id),
                "terminal_name": f"Caja {idx:02d} — {b.name}",
                "branch_id": str(b.id),
                "branch_name": b.name,
                "is_open": True,
                "status_label": "En Sesión (Abierta)",
                "cashier_name": active_s.user.full_name if active_s.user else "Cajero Activo",
                "opened_at": to_local_dt(active_s.opened_at).strftime("%d/%m/%Y %H:%M") if active_s.opened_at else "Hoy",
                "session_number": active_s.session_number,
                "opening_balance": fin["opening"],
                "total_sales": fin["sales_total"],
                "expected_cash": fin["expected_cash"],
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
            "created_at": to_local_dt(m.created_at).strftime("%H:%M:%S") if m.created_at else ""
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
            "time": to_local_dt(o.sale_date or o.created_at).strftime("%H:%M:%S") if (o.sale_date or o.created_at) else "",
            "fel_uuid": o.fel_uuid,
            "fel_number": o.fel_number
        }
        for o in orders
    ]

