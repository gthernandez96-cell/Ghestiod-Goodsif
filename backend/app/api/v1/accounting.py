"""API v1 — Contabilidad y Finanzas ERP 360°."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.accounting import Account, JournalEntry, JournalEntryLine

router = APIRouter(prefix="/accounting")


# ===== SCHEMAS =====
class AccountCreateIn(BaseModel):
    code: str
    name: str
    account_type: str  # asset, liability, equity, income, cost, expense


class JournalEntryLineIn(BaseModel):
    account_id: str
    debit: float = 0.0
    credit: float = 0.0
    notes: Optional[str] = None


class JournalEntryCreateIn(BaseModel):
    concept: str
    journal: str = "Operaciones Varias"
    entry_date: Optional[str] = None
    lines: List[JournalEntryLineIn] = []


# ===== DEFAULT GUATEMALAN STANDARD CHART OF ACCOUNTS =====
DEFAULT_ACCOUNTS = [
    # 1. ACTIVOS
    ("1101", "Caja General y Efectivo", "asset"),
    ("1102", "Bancos y Cuentas Monetarias", "asset"),
    ("1105", "Clientes y Cuentas por Cobrar", "asset"),
    ("1110", "Inventario de Mercaderías", "asset"),
    ("1120", "IVA Crédito Fiscal (Compras)", "asset"),
    ("1201", "Mobiliario y Equipo", "asset"),
    ("1205", "Equipo de Cómputo", "asset"),

    # 2. PASIVOS
    ("2101", "Proveedores y Cuentas por Pagar", "liability"),
    ("2105", "IVA Débito Fiscal por Pagar (Ventas)", "liability"),
    ("2110", "Sueldos y Planillas por Pagar", "liability"),
    ("2115", "Retenciones de Impuestos por Pagar (SAT)", "liability"),

    # 3. CAPITAL / PATRIMONIO
    ("3101", "Capital Social Pagado", "equity"),
    ("3201", "Utilidades Acumuladas de Ejercicios Anteriores", "equity"),
    ("3301", "Utilidad / Pérdida del Ejercicio", "equity"),

    # 4. INGRESOS
    ("4101", "Ventas de Mercaderías / POS", "income"),
    ("4201", "Otros Ingresos Operacionales", "income"),

    # 5. COSTOS
    ("5101", "Costo de Ventas (COGS)", "cost"),
    ("5105", "Materia Prima e Insumos Directos", "cost"),

    # 6. GASTOS DE OPERACIÓN
    ("6101", "Sueldos, Salarios y Prestaciones", "expense"),
    ("6102", "Alquileres de Locales", "expense"),
    ("6103", "Servicios Básicos (Agua, Luz, Internet)", "expense"),
    ("6104", "Gastos de Mantenimiento y Limpieza", "expense"),
    ("6105", "Servicios Contables y Legales", "expense"),
]


async def _ensure_chart_of_accounts(db: AsyncSession, company_id: uuid.UUID):
    """Inicializar automáticamente el catálogo contable de Guatemala."""
    existing_stmt = select(Account.code).where(
        Account.company_id == company_id,
        Account.is_deleted == False
    )
    res = await db.execute(existing_stmt)
    existing_codes = set(res.scalars().all())

    for code, name, acc_type in DEFAULT_ACCOUNTS:
        if code not in existing_codes:
            acc = Account(
                company_id=company_id,
                code=code,
                name=name,
                account_type=acc_type
            )
            db.add(acc)
    await db.commit()


# ===== 1. KPIS FINANCIEROS =====
@router.get("/kpis")
async def get_accounting_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Métricas ejecutivas de la contabilidad."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _ensure_chart_of_accounts(db, c_uuid)

    # Calcular ingresos, costos, gastos y activos acumulados
    stmt = (
        select(
            Account.account_type,
            func.sum(JournalEntryLine.debit).label("total_debit"),
            func.sum(JournalEntryLine.credit).label("total_credit")
        )
        .join(Account, JournalEntryLine.account_id == Account.id)
        .join(JournalEntry, JournalEntryLine.entry_id == JournalEntry.id)
        .where(
            Account.company_id == c_uuid,
            JournalEntry.state == "posted",
            JournalEntry.is_deleted == False
        )
        .group_by(Account.account_type)
    )
    res = await db.execute(stmt)
    rows = res.all()

    type_map = {}
    for r in rows:
        dr = float(r.total_debit or 0.0)
        cr = float(r.total_credit or 0.0)
        t = r.account_type
        # Para income: saldo = credit - debit
        # Para cost/expense/asset: saldo = debit - credit
        if t == "income":
            type_map[t] = cr - dr
        elif t in ["cost", "expense", "asset"]:
            type_map[t] = dr - cr
        elif t in ["liability", "equity"]:
            type_map[t] = cr - dr

    total_income = type_map.get("income", 0.0)
    total_cogs = type_map.get("cost", 0.0)
    total_expenses = type_map.get("expense", 0.0)
    gross_profit = total_income - total_cogs
    net_profit = gross_profit - total_expenses
    total_assets = type_map.get("asset", 0.0)

    # Contar asientos
    entries_count = await db.scalar(select(func.count(JournalEntry.id)).where(
        JournalEntry.company_id == c_uuid,
        JournalEntry.is_deleted == False
    ))

    return {
        "total_income": total_income,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "total_assets": total_assets,
        "entries_count": int(entries_count or 0),
    }


# ===== 2. CHART OF ACCOUNTS (CATÁLOGO DE CUENTAS) =====
@router.get("/accounts")
async def list_accounts(
    account_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar el Catálogo de Cuentas Contables con saldos en tiempo real."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _ensure_chart_of_accounts(db, c_uuid)

    stmt = select(Account).where(
        Account.company_id == c_uuid,
        Account.is_deleted == False
    ).order_by(Account.code)

    if account_type:
        stmt = stmt.where(Account.account_type == account_type)

    if search:
        s_pat = f"%{search}%"
        stmt = stmt.where(
            Account.name.ilike(s_pat) | Account.code.ilike(s_pat)
        )

    res = await db.execute(stmt)
    accounts = res.scalars().all()

    # Obtener saldos de cada cuenta
    bal_stmt = (
        select(
            JournalEntryLine.account_id,
            func.sum(JournalEntryLine.debit).label("debit"),
            func.sum(JournalEntryLine.credit).label("credit")
        )
        .join(JournalEntry)
        .where(
            JournalEntry.company_id == c_uuid,
            JournalEntry.state == "posted",
            JournalEntry.is_deleted == False
        )
        .group_by(JournalEntryLine.account_id)
    )
    bal_res = await db.execute(bal_stmt)
    bal_map = {row.account_id: (float(row.debit or 0), float(row.credit or 0)) for row in bal_res.all()}

    items = []
    for acc in accounts:
        dr, cr = bal_map.get(acc.id, (0.0, 0.0))
        t = acc.account_type or "asset"
        if t in ["asset", "cost", "expense"]:
            balance = dr - cr
        else:
            balance = cr - dr

        items.append({
            "id": str(acc.id),
            "code": acc.code,
            "name": acc.name,
            "account_type": t,
            "total_debit": dr,
            "total_credit": cr,
            "balance": balance
        })

    return items


@router.post("/accounts")
async def create_account(
    data: AccountCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva cuenta en el catálogo contable."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    # Verificar si ya existe el código
    check_stmt = select(Account).where(
        Account.code == data.code,
        Account.company_id == c_uuid,
        Account.is_deleted == False
    )
    chk_res = await db.execute(check_stmt)
    if chk_res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Ya existe una cuenta con el código {data.code}")

    acc = Account(
        company_id=c_uuid,
        code=data.code,
        name=data.name,
        account_type=data.account_type
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)

    return {"id": str(acc.id), "code": acc.code, "name": acc.name, "account_type": acc.account_type}


# ===== 3. JOURNAL ENTRIES (LIBRO DIARIO) =====
@router.get("/entries")
async def list_journal_entries(
    search: Optional[str] = Query(None),
    journal: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar asientos del Libro Diario."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _ensure_chart_of_accounts(db, c_uuid)

    stmt = (
        select(JournalEntry)
        .options(
            selectinload(JournalEntry.lines).selectinload(JournalEntryLine.account)
        )
        .where(
            JournalEntry.company_id == c_uuid,
            JournalEntry.is_deleted == False
        )
        .order_by(JournalEntry.date.desc(), JournalEntry.created_at.desc())
    )

    if journal:
        stmt = stmt.where(JournalEntry.journal == journal)

    res = await db.execute(stmt)
    entries = res.scalars().all()

    items = []
    for e in entries:
        if search:
            q = search.lower()
            if q not in (e.number or "").lower() and q not in (e.concept or "").lower():
                continue

        tot_dr = sum(float(l.debit or 0) for l in e.lines)
        tot_cr = sum(float(l.credit or 0) for l in e.lines)

        items.append({
            "id": str(e.id),
            "number": e.number,
            "journal": e.journal,
            "concept": e.concept,
            "date": e.date.strftime("%d/%m/%Y") if e.date else "",
            "debit": tot_dr,
            "credit": tot_cr,
            "state": e.state or "posted",
            "lines": [
                {
                    "account_code": l.account.code if l.account else "N/A",
                    "account_name": l.account.name if l.account else "Cuenta",
                    "debit": float(l.debit or 0),
                    "credit": float(l.credit or 0),
                }
                for l in e.lines
            ]
        })

    return items


@router.post("/entries")
async def create_journal_entry(
    data: JournalEntryCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear un asiento contable manual multi-línea con validación de partida doble."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _ensure_chart_of_accounts(db, c_uuid)

    if not data.lines or len(data.lines) < 2:
        raise HTTPException(status_code=400, detail="El asiento debe tener al menos 2 líneas de apunte contable.")

    # Validar partida doble (Debe == Haber)
    tot_debit = sum(Decimal(str(l.debit or 0.0)) for l in data.lines)
    tot_credit = sum(Decimal(str(l.credit or 0.0)) for l in data.lines)

    if abs(tot_debit - tot_credit) > Decimal("0.01"):
        raise HTTPException(
            status_code=400,
            detail=f"Asiento descuadrado (Partida no balanceada): Total Debe (Q{tot_debit:.2f}) != Total Haber (Q{tot_credit:.2f})"
        )

    # Generar Secuencia ASI-XXXX
    count_stmt = select(func.count(JournalEntry.id)).where(JournalEntry.company_id == c_uuid)
    count_res = await db.execute(count_stmt)
    seq = (count_res.scalar() or 0) + 1
    now = datetime.now(timezone.utc)

    e_date = now
    if data.entry_date:
        try:
            e_date = datetime.strptime(data.entry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass

    entry = JournalEntry(
        company_id=c_uuid,
        number=f"ASI-{now.year}-{seq:05d}",
        journal=data.journal or "Operaciones Varias",
        concept=data.concept,
        date=e_date,
        state="posted"
    )
    db.add(entry)
    await db.flush()

    for line_in in data.lines:
        try:
            acc_uuid = uuid.UUID(line_in.account_id)
        except ValueError:
            # Buscar por código si no es UUID
            acc_stmt = select(Account.id).where(
                Account.code == line_in.account_id,
                Account.company_id == c_uuid
            ).limit(1)
            acc_res = await db.execute(acc_stmt)
            acc_uuid = acc_res.scalar_one_or_none()
            if not acc_uuid:
                continue

        line = JournalEntryLine(
            entry_id=entry.id,
            account_id=acc_uuid,
            debit=Decimal(str(line_in.debit or 0.0)),
            credit=Decimal(str(line_in.credit or 0.0))
        )
        db.add(line)

    await db.commit()
    await db.refresh(entry)

    return {
        "id": str(entry.id),
        "number": entry.number,
        "concept": entry.concept,
        "debit": float(tot_debit),
        "credit": float(tot_credit),
        "state": "posted"
    }


# ===== 4. TRIAL BALANCE (BALANCE DE COMPROBACIÓN Y SALDOS) =====
@router.get("/trial-balance")
async def get_trial_balance(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generar Balance de Saldos y Comprobación."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _ensure_chart_of_accounts(db, c_uuid)

    accounts_stmt = select(Account).where(
        Account.company_id == c_uuid,
        Account.is_deleted == False
    ).order_by(Account.code)
    acc_res = await db.execute(accounts_stmt)
    accounts = acc_res.scalars().all()

    bal_stmt = (
        select(
            JournalEntryLine.account_id,
            func.sum(JournalEntryLine.debit).label("debit"),
            func.sum(JournalEntryLine.credit).label("credit")
        )
        .join(JournalEntry)
        .where(
            JournalEntry.company_id == c_uuid,
            JournalEntry.state == "posted",
            JournalEntry.is_deleted == False
        )
        .group_by(JournalEntryLine.account_id)
    )
    bal_res = await db.execute(bal_stmt)
    bal_map = {row.account_id: (float(row.debit or 0), float(row.credit or 0)) for row in bal_res.all()}

    items = []
    tot_debit_sum = 0.0
    tot_credit_sum = 0.0
    tot_debtor_bal = 0.0
    tot_creditor_bal = 0.0

    for acc in accounts:
        dr, cr = bal_map.get(acc.id, (0.0, 0.0))
        if dr == 0.0 and cr == 0.0:
            continue

        tot_debit_sum += dr
        tot_credit_sum += cr

        diff = dr - cr
        debtor = diff if diff > 0 else 0.0
        creditor = abs(diff) if diff < 0 else 0.0

        tot_debtor_bal += debtor
        tot_creditor_bal += creditor

        items.append({
            "code": acc.code,
            "name": acc.name,
            "account_type": acc.account_type,
            "debit_moves": dr,
            "credit_moves": cr,
            "debtor_balance": debtor,
            "creditor_balance": creditor
        })

    return {
        "items": items,
        "totals": {
            "debit_moves": tot_debit_sum,
            "credit_moves": tot_credit_sum,
            "debtor_balance": tot_debtor_bal,
            "creditor_balance": tot_creditor_bal,
            "is_balanced": abs(tot_debtor_bal - tot_creditor_bal) < 0.01
        }
    }


# ===== 5. INCOME STATEMENT (ESTADO DE RESULTADOS / P&L) =====
@router.get("/income-statement")
async def get_income_statement(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generar Estado de Resultados (Pérdidas y Ganancias) en vivo."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await _ensure_chart_of_accounts(db, c_uuid)

    stmt = (
        select(
            Account.code,
            Account.name,
            Account.account_type,
            func.sum(JournalEntryLine.debit).label("debit"),
            func.sum(JournalEntryLine.credit).label("credit")
        )
        .join(Account, JournalEntryLine.account_id == Account.id)
        .join(JournalEntry, JournalEntryLine.entry_id == JournalEntry.id)
        .where(
            Account.company_id == c_uuid,
            Account.account_type.in_(["income", "cost", "expense"]),
            JournalEntry.state == "posted",
            JournalEntry.is_deleted == False
        )
        .group_by(Account.id, Account.code, Account.name, Account.account_type)
        .order_by(Account.code)
    )
    res = await db.execute(stmt)
    rows = res.all()

    incomes = []
    costs = []
    expenses = []

    total_incomes = 0.0
    total_costs = 0.0
    total_expenses = 0.0

    for r in rows:
        dr = float(r.debit or 0.0)
        cr = float(r.credit or 0.0)
        t = r.account_type

        if t == "income":
            net = cr - dr
            total_incomes += net
            incomes.append({"code": r.code, "name": r.name, "amount": net})
        elif t == "cost":
            net = dr - cr
            total_costs += net
            costs.append({"code": r.code, "name": r.name, "amount": net})
        elif t == "expense":
            net = dr - cr
            total_expenses += net
            expenses.append({"code": r.code, "name": r.name, "amount": net})

    gross_profit = total_incomes - total_costs
    net_profit = gross_profit - total_expenses
    margin_pct = (net_profit / total_incomes * 100.0) if total_incomes > 0 else 0.0

    return {
        "incomes": incomes,
        "total_incomes": total_incomes,
        "costs": costs,
        "total_costs": total_costs,
        "gross_profit": gross_profit,
        "expenses": expenses,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "margin_pct": round(margin_pct, 1)
    }
