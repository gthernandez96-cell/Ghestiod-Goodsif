"""Modelos: Documentos FEL (Factura Electrónica en Línea Guatemala)."""
from sqlalchemy import Column, String, Boolean, ForeignKey, Text, Numeric, Enum as SAEnum, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class DTEType(str, enum.Enum):
    INVOICE = "FACT"              # Factura
    EXCHANGE_INVOICE = "FCAM"    # Factura Cambiaria
    SPECIAL_INVOICE = "FESP"     # Factura Especial
    CREDIT_NOTE = "NCRE"         # Nota de Crédito
    DEBIT_NOTE = "NDEB"          # Nota de Débito
    EXPORT_INVOICE = "FEXP"      # Factura de Exportación


class FELStatus(str, enum.Enum):
    PENDING = "pending"           # Pendiente de certificar
    CERTIFIED = "certified"       # Certificada por SAT
    CANCELLED = "cancelled"       # Anulada
    ERROR = "error"               # Error en certificación
    CONTINGENCY = "contingency"   # En contingencia (sin internet)


class FELDocument(BaseModel):
    """Documento Tributario Electrónico (DTE) FEL Guatemala."""
    __tablename__ = "fel_documents"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sale_orders.id"), nullable=True)

    # Tipo de DTE
    dte_type = Column(SAEnum(DTEType), default=DTEType.INVOICE)
    status = Column(SAEnum(FELStatus), default=FELStatus.PENDING)

    # Datos del emisor
    issuer_nit = Column(String(20), nullable=False)
    issuer_name = Column(String(300), nullable=False)
    establishment_code = Column(String(20))       # Código establecimiento SAT
    issuer_address = Column(Text)

    # Datos del receptor
    receiver_nit = Column(String(20), nullable=False)  # CF para consumidor final
    receiver_name = Column(String(300), nullable=False)
    receiver_address = Column(Text)
    receiver_email = Column(String(200))

    # Numeración interna
    internal_number = Column(String(50), nullable=False)  # Número secuencial interno

    # Respuesta del SAT/Certificador
    uuid = Column(String(200), unique=True, index=True)   # UUID del DTE
    authorization_number = Column(String(200))             # Número de autorización
    series = Column(String(50))                            # Serie
    number = Column(String(50))                            # Número correlativo
    certified_at = Column(DateTime(timezone=True))

    # Montos
    subtotal = Column(Numeric(18, 4), default=0)
    tax_amount = Column(Numeric(18, 4), default=0)
    total = Column(Numeric(18, 4), default=0)
    currency = Column(String(10), default="GTQ")

    # Documentos
    xml_content = Column(Text)           # XML firmado del DTE
    pdf_url = Column(String(500))        # URL del PDF
    qr_url = Column(String(500))         # URL del QR para verificación SAT

    # Para notas de crédito/débito
    related_uuid = Column(String(200))   # UUID del documento relacionado

    # Contingencia
    contingency_reason = Column(String(500))
    synced_at = Column(DateTime(timezone=True))  # Cuando fue sincronizado

    # Error
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)

    # Metadata adicional del proveedor FEL
    provider_response = Column(JSONB, default=dict)

    # Relationships
    sale = relationship("SaleOrder")

    def __repr__(self):
        return f"<FELDocument {self.dte_type} {self.uuid or 'PENDIENTE'}>"


class FELSequence(BaseModel):
    """Secuencia numérica para documentos internos por tipo."""
    __tablename__ = "fel_sequences"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    dte_type = Column(SAEnum(DTEType), nullable=False)
    prefix = Column(String(20), default="")
    last_number = Column(Integer, default=0)
    padding = Column(Integer, default=8)  # Ceros a la izquierda

    def next_number(self) -> str:
        self.last_number += 1
        return f"{self.prefix}{str(self.last_number).zfill(self.padding)}"
