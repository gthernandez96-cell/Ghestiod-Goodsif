"""Modelos: Empresa y Sucursales."""
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, Text, Numeric, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class TaxRegime(str, enum.Enum):
    GENERAL = "general"
    SMALL_TAXPAYER = "pequeno_contribuyente"


class Company(BaseModel):
    """Empresa / Tenant principal."""
    __tablename__ = "companies"

    name = Column(String(200), nullable=False)
    trade_name = Column(String(200))
    nit = Column(String(20), nullable=False, unique=True)
    tax_regime = Column(SAEnum(TaxRegime), default=TaxRegime.GENERAL)
    address = Column(Text)
    city = Column(String(100))
    country = Column(String(100), default="Guatemala")
    phone = Column(String(30))
    email = Column(String(200))
    website = Column(String(200))
    logo_url = Column(String(500))
    currency = Column(String(10), default="GTQ")
    timezone = Column(String(50), default="America/Guatemala")
    settings = Column(JSONB, default=dict)
    is_active = Column(Boolean, default=True)

    # Relationships
    branches = relationship("Branch", back_populates="company", lazy="selectin")
    users = relationship("User", back_populates="company")

    def __repr__(self):
        return f"<Company {self.name} NIT:{self.nit}>"


class Branch(BaseModel):
    """Sucursal de una empresa."""
    __tablename__ = "branches"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    name = Column(String(200), nullable=False)
    code = Column(String(20), nullable=False)  # Código interno: SUC001
    address = Column(Text)
    city = Column(String(100))
    phone = Column(String(30))
    email = Column(String(200))
    manager_name = Column(String(200))
    is_active = Column(Boolean, default=True)
    is_main = Column(Boolean, default=False)  # Sucursal principal
    settings = Column(JSONB, default=dict)  # Config específica de sucursal

    # FEL por sucursal
    fel_establishment_code = Column(String(20))  # Código establecimiento SAT
    fel_address = Column(Text)  # Dirección para facturas

    # Relationships
    company = relationship("Company", back_populates="branches")
    warehouses = relationship("Warehouse", back_populates="branch", lazy="selectin")
    pos_sessions = relationship("POSSession", back_populates="branch")
    cashier_sessions = relationship("CashierSession", back_populates="branch")

    def __repr__(self):
        return f"<Branch {self.code} - {self.name}>"


class Warehouse(BaseModel):
    """Bodega/Almacén de una sucursal."""
    __tablename__ = "warehouses"

    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    name = Column(String(200), nullable=False)
    code = Column(String(20), nullable=False)
    address = Column(Text)
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)

    # Relationships
    branch = relationship("Branch", back_populates="warehouses")
    stock_moves = relationship("StockMove", back_populates="warehouse")
    stock_lots = relationship("StockLot", back_populates="warehouse")

    def __repr__(self):
        return f"<Warehouse {self.code} - {self.name}>"
