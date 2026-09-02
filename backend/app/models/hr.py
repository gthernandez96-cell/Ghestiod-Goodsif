"""Modelos: Empleados y Control de Asistencia (HR)."""
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class Employee(BaseModel):
    """Empleado / Colaborador de la empresa."""
    __tablename__ = "employees"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    name = Column(String(300), nullable=False, index=True)
    role = Column(String(100), nullable=False)
    shift = Column(String(100))
    phone = Column(String(50))
    present = Column(Boolean, default=False, nullable=False)

    # Relationships
    attendances = relationship("Attendance", back_populates="employee", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Employee {self.name} - {self.role}>"


class Attendance(BaseModel):
    """Registro de asistencia (Check-in/Check-out)."""
    __tablename__ = "attendances"

    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=False)
    check_in = Column(DateTime(timezone=True), nullable=False)
    check_out = Column(DateTime(timezone=True), nullable=True)
    date = Column(DateTime(timezone=True), nullable=False)

    # Relationships
    employee = relationship("Employee", back_populates="attendances")

    def __repr__(self):
        return f"<Attendance {self.employee_id} - In: {self.check_in}>"
