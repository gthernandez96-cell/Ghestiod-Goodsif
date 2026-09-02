"""Modelos: Usuarios, Roles y Permisos."""
from sqlalchemy import Column, String, Boolean, ForeignKey, Table, Text
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import relationship
from app.models.base import BaseModel
from app.core.database import Base
import uuid


# Tabla de asociación usuario-roles
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True),
    extend_existing=True,
)

# Tabla de asociación usuario-sucursales
user_branches = Table(
    "user_branches",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("branch_id", UUID(as_uuid=True), ForeignKey("branches.id"), primary_key=True),
    extend_existing=True,
)


class Permission(BaseModel):
    """Permiso granular del sistema."""
    __tablename__ = "permissions"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    name = Column(String(200), nullable=False)
    code = Column(String(100), nullable=False)
    module = Column(String(100), nullable=False)
    description = Column(Text)

    # Relationships
    roles = relationship("Role", secondary="role_permissions", back_populates="permissions")


# Tabla de asociación rol-permisos
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", UUID(as_uuid=True), ForeignKey("permissions.id"), primary_key=True),
    extend_existing=True,
)


class Role(BaseModel):
    """Rol con conjunto de permisos."""
    __tablename__ = "roles"

    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False)  # ej: "cashier", "admin"
    description = Column(Text)
    is_system = Column(Boolean, default=False)  # Roles del sistema (no editables)

    # Relationships
    permissions = relationship("Permission", secondary="role_permissions", back_populates="roles")
    users = relationship("User", secondary="user_roles", back_populates="roles")

    def get_permission_codes(self) -> list[str]:
        return [p.code for p in self.permissions]


class User(BaseModel):
    """Usuario del sistema."""
    __tablename__ = "users"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    email = Column(String(200), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    full_name = Column(String(200), nullable=False)
    hashed_password = Column(String(200), nullable=False)
    phone = Column(String(30))
    avatar_url = Column(String(500))

    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)

    # Sucursal actual activa del usuario
    current_branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True)

    # Settings personales
    settings = Column(JSONB, default=dict)

    # Relationships
    company = relationship("Company", back_populates="users")
    roles = relationship("Role", secondary="user_roles", back_populates="users")
    branches = relationship("Branch", secondary="user_branches")
    cashier_sessions = relationship("CashierSession", back_populates="user")

    def get_permissions(self) -> list[str]:
        """Obtener todos los códigos de permisos del usuario."""
        perms = set()
        for role in self.roles:
            perms.update(role.get_permission_codes())
        return list(perms)

    def has_permission(self, permission_code: str) -> bool:
        if self.is_superuser:
            return True
        return permission_code in self.get_permissions()

    def has_branch_access(self, branch_id: str) -> bool:
        if self.is_superuser:
            return True
        branch_ids = [str(b.id) for b in self.branches]
        return str(branch_id) in branch_ids

    def __repr__(self):
        return f"<User {self.username} - {self.email}>"
