"""Modelos: Sesión de caja, apertura/cierre y movimientos."""
from sqlalchemy import Column, String, Boolean, ForeignKey, Text, Numeric, Enum as SAEnum, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class SessionState(str, enum.Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class CashMovementType(str, enum.Enum):
    OPEN_BALANCE = "open_balance"   # Saldo de apertura
    CASH_IN = "cash_in"             # Entrada de efectivo
    CASH_OUT = "cash_out"           # Salida de efectivo
    SALE = "sale"                   # Venta
    RETURN = "return"               # Devolución
    CLOSE_BALANCE = "close_balance" # Saldo de cierre


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    CARD = "card"           # Tarjeta de crédito/débito
    TRANSFER = "transfer"   # Transferencia bancaria
    CHECK = "check"         # Cheque
    QR = "qr"               # Pago QR (VISA QR, etc.)
    CREDIT = "credit"       # Crédito del cliente
    OTHER = "other"


class CashierSession(BaseModel):
    """Sesión de caja (turno de cajero)."""
    __tablename__ = "cashier_sessions"

    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    pos_id = Column(UUID(as_uuid=True), ForeignKey("pos_terminals.id"), nullable=True)

    state = Column(SAEnum(SessionState), default=SessionState.OPEN)
    session_number = Column(String(50), unique=True, nullable=False)

    # Apertura
    opened_at = Column(DateTime(timezone=True))
    opening_balance = Column(Numeric(18, 4), default=0)
    opening_notes = Column(Text)
    opening_denominations = Column(JSONB, default=dict)  # {"Q100": 5, "Q50": 10, ...}

    # Cierre
    closed_at = Column(DateTime(timezone=True))
    closing_balance_expected = Column(Numeric(18, 4), default=0)
    closing_balance_real = Column(Numeric(18, 4), default=0)
    closing_difference = Column(Numeric(18, 4), default=0)
    closing_denominations = Column(JSONB, default=dict)
    closing_notes = Column(Text)

    # Totales de la sesión
    total_sales = Column(Numeric(18, 4), default=0)
    total_returns = Column(Numeric(18, 4), default=0)
    total_cash_in = Column(Numeric(18, 4), default=0)
    total_cash_out = Column(Numeric(18, 4), default=0)

    # Relaciones
    branch = relationship("Branch", back_populates="cashier_sessions")
    user = relationship("User", back_populates="cashier_sessions")
    movements = relationship("CashMovement", back_populates="session", lazy="selectin")
    sales = relationship("SaleOrder", back_populates="cashier_session")

    def __repr__(self):
        return f"<CashierSession {self.session_number} - {self.state}>"


class POSTerminal(BaseModel):
    """Terminal POS (caja física)."""
    __tablename__ = "pos_terminals"

    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    name = Column(String(100), nullable=False)  # "Caja 1", "Caja Principal"
    code = Column(String(20), nullable=False)
    is_active = Column(Boolean, default=True)

    # Configuración por terminal
    printer_ip = Column(String(50))
    printer_port = Column(Integer, default=9100)
    display_customer = Column(Boolean, default=False)  # Visor de cliente
    settings = Column(JSONB, default=dict)

    branch = relationship("Branch")
    sessions = relationship("CashierSession", back_populates="pos_terminal" if False else None)

    __mapper_args__ = {"eager_defaults": True}


class CashMovement(BaseModel):
    """Movimiento de caja (entrada/salida de efectivo)."""
    __tablename__ = "cash_movements"

    session_id = Column(UUID(as_uuid=True), ForeignKey("cashier_sessions.id"), nullable=False)
    move_type = Column(SAEnum(CashMovementType), nullable=False)
    payment_method = Column(SAEnum(PaymentMethod), default=PaymentMethod.CASH)
    amount = Column(Numeric(18, 4), nullable=False)
    reference = Column(String(200))
    notes = Column(Text)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Relationships
    session = relationship("CashierSession", back_populates="movements")

    def __repr__(self):
        return f"<CashMovement {self.move_type} Q{self.amount}>"
