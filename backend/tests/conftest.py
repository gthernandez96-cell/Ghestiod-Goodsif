"""Configuración global y fixtures para pytest."""
import os
import sys

# Configurar variables de entorno para usar una base de datos aislada de pruebas
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_pos.db"
os.environ["DATABASE_URL_SYNC"] = "sqlite:///./test_pos.db"
os.environ["SECRET_KEY"] = "clave-secreta-pos-2024"

_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import pytest
import asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select
from app.core.database import engine, Base
from app.models import Company, Branch, Warehouse
import uuid

COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Garantizar que todas las tablas y datos base existan antes de ejecutar los tests."""
    from app.core.security import get_password_hash
    from app.models import User, Role

    async def _init():
        import app.models  # noqa
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            comp_res = await session.execute(select(Company).where(Company.id == COMPANY_ID))
            comp = comp_res.scalar_one_or_none()
            if not comp:
                comp = Company(id=COMPANY_ID, name="Empresa Test Global", nit="12345678-9")
                session.add(comp)
                await session.flush()

            b_existing = await session.execute(select(Branch).where(Branch.company_id == COMPANY_ID))
            branch = b_existing.scalars().first()
            if not branch:
                branch = Branch(
                    company_id=COMPANY_ID,
                    name="Sucursal Principal Test",
                    code="SUC-TEST-001",
                    is_main=True
                )
                session.add(branch)
                await session.flush()

            wh_existing = await session.execute(select(Warehouse).where(Warehouse.branch_id == branch.id))
            if not wh_existing.scalars().first():
                wh = Warehouse(
                    branch_id=branch.id,
                    name="Bodega Principal Test",
                    code="BOD-TEST-001",
                    is_default=True
                )
                session.add(wh)
                await session.flush()

            existing = await session.execute(select(User).where(User.id == COMPANY_ID))
            if not existing.scalar_one_or_none():
                user = User(
                    id=COMPANY_ID,
                    company_id=COMPANY_ID,
                    current_branch_id=branch.id,
                    email="test_admin@empresa.com",
                    username="test_admin",
                    full_name="Admin Test",
                    hashed_password=get_password_hash("Admin2024!"),
                    is_active=True,
                    is_superuser=True,
                )
                session.add(user)

            await session.commit()

    asyncio.run(_init())
