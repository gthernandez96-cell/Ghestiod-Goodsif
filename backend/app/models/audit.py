"""Modelos: Bitácora de Auditoría y Seguridad."""
from sqlalchemy import Column, String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import BaseModel


class AuditLog(BaseModel):
    """Bitácora para el registro de actividades y eventos críticos de seguridad."""
    __tablename__ = "audit_logs"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    module = Column(String(100), nullable=False)
    action = Column(String(500), nullable=False)
    ip_address = Column(String(50))
    state = Column(String(20), default="valid", nullable=False)  # valid, warning, anomaly

    def __repr__(self):
        return f"<AuditLog {self.module} - {self.action}>"
