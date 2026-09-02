from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, MappedColumn
from sqlalchemy import text
from typing import AsyncGenerator
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# Async engine configuration depending on DB backend
engine_kwargs = {
    "echo": settings.DEBUG,
}
if "sqlite" not in settings.DATABASE_URL:
    engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 40,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    })

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos ORM."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency para inyectar sesión de BD en FastAPI."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_tables():
    """Crear todas las tablas (solo para desarrollo/testing)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Tablas creadas correctamente")


async def check_db_connection():
    """Verificar conexión a la base de datos y sincronizar tablas y columnas."""
    try:
        # Importar modelos para que Base.metadata los reconozca
        import app.models  # noqa
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.run_sync(Base.metadata.create_all)
            try:
                await conn.execute(text("ALTER TABLE products ADD COLUMN supplier_id VARCHAR(36)"))
            except Exception:
                pass
            try:
                await conn.execute(text("ALTER TABLE partners ADD COLUMN image_url VARCHAR(500)"))
            except Exception:
                pass
        logger.info("✅ Conexión a base de datos establecida y tablas sincronizadas")
        await ensure_initial_bootstrap()
        return True
    except Exception as e:
        logger.error(f"❌ Error conectando a base de datos: {e}")
        return False


async def ensure_initial_bootstrap():
    """Inicializar automáticamente empresa, sucursal, bodega, roles y admin inicial si no hay usuarios."""
    try:
        from app.models.user import User, Role
        from app.models.company import Company, Branch, Warehouse
        from app.core.security import get_password_hash
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            user_exists = (await db.execute(select(User).limit(1))).scalar_one_or_none()
            if user_exists:
                return

            logger.info("⚡ Base de datos nueva detectada. Ejecutando bootstrap inicial...")
            # 1. Empresa
            comp = (await db.execute(select(Company).limit(1))).scalar_one_or_none()
            if not comp:
                comp = Company(
                    name=settings.INIT_COMPANY_NAME,
                    trade_name=settings.INIT_COMPANY_NAME,
                    nit=settings.INIT_COMPANY_NIT,
                    address="Ciudad de Guatemala",
                    city="Guatemala",
                    country="Guatemala",
                    currency="GTQ",
                )
                db.add(comp)
                await db.flush()

            # 2. Sucursal Principal
            branch = (await db.execute(select(Branch).where(Branch.company_id == comp.id).limit(1))).scalar_one_or_none()
            if not branch:
                branch = Branch(
                    company_id=comp.id,
                    name="Sucursal Principal",
                    code="SUC001",
                    is_active=True,
                    is_main=True,
                    fel_establishment_code="1",
                )
                db.add(branch)
                await db.flush()

            # 3. Bodega Principal
            wh = (await db.execute(select(Warehouse).where(Warehouse.branch_id == branch.id).limit(1))).scalar_one_or_none()
            if not wh:
                wh = Warehouse(
                    branch_id=branch.id,
                    name="Bodega Central",
                    code="BOD001",
                    is_active=True,
                    is_default=True,
                )
                db.add(wh)
                await db.flush()

            # 4. Roles básicos
            roles_def = [
                ("Administrador", "admin", "Acceso total al sistema"),
                ("Gerente de Tienda", "store_manager", "Gestión de sucursal e inventarios"),
                ("Cajero", "cashier", "Operaciones de venta y cobro"),
                ("Bodeguero", "warehouse", "Control de existencias y traslados"),
                ("Contador", "accountant", "Reportes contables y FEL"),
            ]
            admin_role = None
            for r_name, r_code, r_desc in roles_def:
                r_match = (await db.execute(select(Role).where(Role.code == r_code))).scalar_one_or_none()
                if not r_match:
                    r_match = Role(company_id=comp.id, name=r_name, code=r_code, description=r_desc)
                    db.add(r_match)
                    await db.flush()
                if r_code == "admin":
                    admin_role = r_match

            # 5. Usuario Administrador
            admin_user = User(
                company_id=comp.id,
                username="admin",
                email=settings.INIT_ADMIN_EMAIL,
                full_name="Administrador del Sistema",
                hashed_password=get_password_hash(settings.INIT_ADMIN_PASSWORD),
                is_active=True,
                is_superuser=True,
                current_branch_id=branch.id,
            )
            if admin_role:
                admin_user.roles.append(admin_role)
            admin_user.branches.append(branch)
            db.add(admin_user)

            # 6. Unidades de Medida Estándar
            from app.models.product import UnitOfMeasure
            standard_uoms = [
                ("Pieza", "Pz", "unit", 1.0, True),
                ("Paquete", "Pqt", "unit", 1.0, False),
                ("Caja", "Caja", "unit", 1.0, False),
                ("Fardo", "Fdo", "unit", 1.0, False),
                ("Docena", "Doc", "unit", 12.0, False),
                ("Libra", "lb", "weight", 0.4536, False),
                ("Kilogramo", "kg", "weight", 1.0, True),
                ("Litro", "L", "volume", 1.0, True),
                ("Galón", "Gal", "volume", 3.785, False),
                ("Metro", "m", "length", 1.0, True)
            ]
            for u_name, u_sym, u_cat, u_ratio, u_is_base in standard_uoms:
                db.add(UnitOfMeasure(
                    company_id=comp.id,
                    name=u_name,
                    symbol=u_sym,
                    category=u_cat,
                    ratio=u_ratio,
                    is_base=u_is_base,
                    is_active=True
                ))

            await db.commit()
            logger.info("✅ Bootstrap inicial completado con éxito.")
    except Exception as e:
        logger.warning(f"⚠️ Aviso durante bootstrap inicial: {e}")


