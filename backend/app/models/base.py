"""Base model con campos comunes para todos los modelos."""
from sqlalchemy import Column, DateTime, Boolean, String, func, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.ext.compiler import compiles
import uuid
from app.core.database import Base
# ===== COMPILATION ADAPTERS FOR SQLITE (DESARROLLO LOCAL SIN POSTGRESQL) =====
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "VARCHAR(36)"


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(type_, compiler, **kw):
    return "JSON"



class TimestampMixin:
    """Mixin con campos de auditoría de tiempo."""
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SoftDeleteMixin:
    """Mixin para borrado lógico."""
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class BaseModel(Base, TimestampMixin, SoftDeleteMixin):
    """Modelo base abstracto con UUID, timestamps y soft delete."""
    __abstract__ = True

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
