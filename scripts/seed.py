#!/usr/bin/env python3
"""Script de inicialización y seed de datos para el sistema POS."""
import asyncio
import sys
import os
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(root_dir, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select
from app.core.config import settings
from app.core.security import get_password_hash
from app.models import *


engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def seed():
    """Crear datos iniciales del sistema."""
    from app.core.database import Base

    print("🔧 Creando tablas...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        print("🏢 Creando empresa...")
        # Verificar si ya existe
        result = await db.execute(select(Company).limit(1))
        if result.scalar_one_or_none():
            print("✅ Datos ya existen. Saltando seed.")
            return

        # Empresa
        company = Company(
            name=settings.INIT_COMPANY_NAME,
            trade_name=settings.INIT_COMPANY_NAME,
            nit=settings.INIT_COMPANY_NIT,
            tax_regime=TaxRegime.GENERAL,
            address="6a Avenida, Guatemala Ciudad",
            city="Guatemala",
            country="Guatemala",
            phone="2222-3333",
            email="info@empresa.com",
            currency="GTQ",
            timezone="America/Guatemala",
        )
        db.add(company)
        await db.flush()

        print("🏪 Creando sucursales...")
        branch_main = Branch(
            company_id=company.id,
            name="Sucursal Central",
            code="SUC001",
            address="6a Avenida 1-36 Zona 1, Guatemala",
            city="Guatemala",
            phone="2222-1111",
            is_active=True,
            is_main=True,
            fel_establishment_code="1",
            fel_address="6a Avenida 1-36 Zona 1, Guatemala",
        )
        branch_2 = Branch(
            company_id=company.id,
            name="Sucursal Norte",
            code="SUC002",
            address="Calzada Roosevelt km 14, Mixco",
            city="Mixco",
            is_active=True,
            is_main=False,
            fel_establishment_code="2",
        )
        branch_3 = Branch(
            company_id=company.id,
            name="Sucursal Sur",
            code="SUC003",
            address="Avenida Hincapié, Villa Nueva",
            city="Villa Nueva",
            is_active=True,
            is_main=False,
            fel_establishment_code="3",
        )
        db.add_all([branch_main, branch_2, branch_3])
        await db.flush()

        print("🏭 Creando bodegas...")
        warehouse_main = Warehouse(
            branch_id=branch_main.id,
            name="Bodega Central",
            code="BOD001",
            is_active=True,
            is_default=True,
        )
        warehouse_2 = Warehouse(
            branch_id=branch_2.id,
            name="Bodega Norte",
            code="BOD002",
            is_active=True,
            is_default=True,
        )
        db.add_all([warehouse_main, warehouse_2])
        await db.flush()

        print("🔐 Creando roles y permisos...")
        # Permisos
        perms_data = [
            ("pos.view", "Ver POS", "pos"),
            ("pos.sale", "Realizar venta", "pos"),
            ("pos.return", "Realizar devolución", "pos"),
            ("pos.discount", "Aplicar descuentos", "pos"),
            ("cashier.open", "Abrir caja", "cashier"),
            ("cashier.close", "Cerrar caja", "cashier"),
            ("cashier.cash_in_out", "Entrada/Salida caja", "cashier"),
            ("inventory.view", "Ver inventario", "inventory"),
            ("inventory.edit", "Editar inventario", "inventory"),
            ("inventory.adjust", "Ajustar inventario", "inventory"),
            ("products.view", "Ver productos", "products"),
            ("products.edit", "Editar productos", "products"),
            ("products.create", "Crear productos", "products"),
            ("products.delete", "Eliminar productos", "products"),
            ("purchases.view", "Ver compras", "purchases"),
            ("purchases.create", "Crear órdenes de compra", "purchases"),
            ("purchases.approve", "Aprobar compras", "purchases"),
            ("transfers.view", "Ver traslados", "transfers"),
            ("transfers.create", "Crear traslados", "transfers"),
            ("transfers.approve", "Aprobar traslados", "transfers"),
            ("transfers.receive", "Recibir traslados", "transfers"),
            ("customers.view", "Ver clientes", "customers"),
            ("customers.edit", "Editar clientes", "customers"),
            ("reports.view", "Ver reportes", "reports"),
            ("reports.export", "Exportar reportes", "reports"),
            ("settings.view", "Ver configuración", "settings"),
            ("settings.edit", "Editar configuración", "settings"),
            ("users.view", "Ver usuarios", "users"),
            ("users.edit", "Editar usuarios", "users"),
            ("fel.view", "Ver documentos FEL", "fel"),
            ("fel.cancel", "Anular documentos FEL", "fel"),
        ]

        permissions = {}
        for code, name, module in perms_data:
            perm = Permission(code=code, name=name, module=module)
            db.add(perm)
            permissions[code] = perm
        await db.flush()

        # Roles
        role_admin = Role(
            name="Administrador",
            code="admin",
            description="Acceso total al sistema",
            is_system=True,
        )
        role_admin.permissions = list(permissions.values())
        db.add(role_admin)

        role_cashier = Role(
            name="Cajero",
            code="cashier",
            description="Operaciones de caja y venta",
            is_system=True,
        )
        cashier_perms = ["pos.view", "pos.sale", "pos.return", "cashier.open", "cashier.close",
                         "cashier.cash_in_out", "inventory.view", "products.view", "customers.view"]
        role_cashier.permissions = [permissions[p] for p in cashier_perms if p in permissions]
        db.add(role_cashier)

        role_inventory = Role(
            name="Encargado de Inventario",
            code="inventory_manager",
            description="Gestión completa de inventario",
            is_system=True,
        )
        inv_perms = ["inventory.view", "inventory.edit", "inventory.adjust", "products.view",
                     "products.edit", "products.create", "transfers.view", "transfers.create",
                     "transfers.receive", "purchases.view", "reports.view"]
        role_inventory.permissions = [permissions[p] for p in inv_perms if p in permissions]
        db.add(role_inventory)

        role_supervisor = Role(
            name="Supervisor",
            code="supervisor",
            description="Supervisión y aprobaciones",
            is_system=True,
        )
        db.add(role_supervisor)
        await db.flush()

        print("👤 Creando usuarios...")
        # Admin
        admin = User(
            company_id=company.id,
            email=settings.INIT_ADMIN_EMAIL,
            username="admin",
            full_name="Administrador del Sistema",
            hashed_password=get_password_hash(settings.INIT_ADMIN_PASSWORD),
            is_active=True,
            is_superuser=True,
            is_verified=True,
            current_branch_id=branch_main.id,
        )
        admin.roles = [role_admin]
        admin.branches = [branch_main, branch_2, branch_3]
        db.add(admin)

        # Cajero demo
        cashier_user = User(
            company_id=company.id,
            email="cajero@empresa.com",
            username="cajero01",
            full_name="María García",
            hashed_password=get_password_hash("Cajero2024!"),
            is_active=True,
            is_superuser=False,
            is_verified=True,
            current_branch_id=branch_main.id,
        )
        cashier_user.roles = [role_cashier]
        cashier_user.branches = [branch_main]
        db.add(cashier_user)
        await db.flush()

        print("📏 Creando unidades de medida...")
        uom_pz = UnitOfMeasure(company_id=company.id, name="Pieza", symbol="Pz", category="unit", is_base=True)
        uom_kg = UnitOfMeasure(company_id=company.id, name="Kilogramo", symbol="kg", category="weight", is_base=True)
        uom_lb = UnitOfMeasure(company_id=company.id, name="Libra", symbol="lb", category="weight", ratio=0.4536)
        uom_lt = UnitOfMeasure(company_id=company.id, name="Litro", symbol="L", category="volume", is_base=True)
        uom_caja = UnitOfMeasure(company_id=company.id, name="Caja", symbol="Caja", category="unit")
        db.add_all([uom_pz, uom_kg, uom_lb, uom_lt, uom_caja])
        await db.flush()

        print("📁 Creando categorías de productos...")
        categories_data = [
            ("Alimentos y Abarrotes", "ALIB"),
            ("Bebidas", "BEB"),
            ("Limpieza y Hogar", "LIMP"),
            ("Farmacia y Salud", "FARM"),
            ("Ferretería", "FERR"),
            ("Tecnología", "TEC"),
            ("Ropa y Calzado", "ROPA"),
        ]
        categories = {}
        for name, code in categories_data:
            cat = ProductCategory(
                company_id=company.id,
                name=name,
                code=code,
                tax_rate=12.00,
                cost_method="average",
            )
            db.add(cat)
            categories[code] = cat
        await db.flush()

        print("💰 Creando listas de precios...")
        pl_retail = PriceList(company_id=company.id, name="Minorista", code="MINORISTA", is_default=True)
        pl_wholesale = PriceList(company_id=company.id, name="Mayorista", code="MAYORISTA")
        pl_special = PriceList(company_id=company.id, name="Precio Especial", code="ESPECIAL")
        db.add_all([pl_retail, pl_wholesale, pl_special])
        await db.flush()

        print("📦 Creando productos de ejemplo...")
        products_data = [
            ("Coca-Cola 600ml", "COC001", "7501035901074", categories["BEB"].id, uom_pz.id, 7.00, 5.00, 12.00, "https://images.unsplash.com/photo-1622483767028-3f66f32aef97?w=120&auto=format&fit=crop"),
            ("Arroz Integral 1kg", "ARR001", "7501234567890", categories["ALIB"].id, uom_kg.id, 15.00, 11.00, 0.00, "https://images.unsplash.com/photo-1586201375761-83865001e31c?w=120&auto=format&fit=crop"),
            ("Leche Entera 1L", "LEC001", "7500028011234", categories["ALIB"].id, uom_lt.id, 12.00, 9.00, 0.00, "https://images.unsplash.com/photo-1563636619-e9143da7973b?w=120&auto=format&fit=crop"),
            ("Aceite Vegetal 1L", "ACE001", "7501056789012", categories["ALIB"].id, uom_lt.id, 25.00, 18.00, 12.00, "https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?w=120&auto=format&fit=crop"),
            ("Jabón Líquido 500ml", "JAB001", "7502134567890", categories["LIMP"].id, uom_pz.id, 28.00, 20.00, 12.00, "https://images.unsplash.com/photo-1584622650111-993a426fbf0a?w=120&auto=format&fit=crop"),
            ("Papel Higiénico x4", "PAP001", "7503456789012", categories["LIMP"].id, uom_pz.id, 18.00, 13.00, 12.00, "https://images.unsplash.com/photo-1584556812952-905ffd0c611a?w=120&auto=format&fit=crop"),
            ("Paracetamol 500mg x20", "PAR001", "7504567890123", categories["FARM"].id, uom_pz.id, 3.50, 2.00, 0.00, "https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?w=120&auto=format&fit=crop"),
            ("Tornillo 1/2\" x100", "TOR001", None, categories["FERR"].id, uom_pz.id, 25.00, 15.00, 12.00, "https://images.unsplash.com/photo-1590483736148-3c1a5e59b993?w=120&auto=format&fit=crop"),
            ("Pintura Blanca 1gal", "PIN001", None, categories["FERR"].id, uom_pz.id, 85.00, 60.00, 12.00, "https://images.unsplash.com/photo-1589939705384-5185137a7f0f?w=120&auto=format&fit=crop"),
            ("Azúcar Blanca 1kg", "AZU001", "7505678901234", categories["ALIB"].id, uom_kg.id, 8.00, 5.50, 0.00, "https://images.unsplash.com/photo-1581798459219-318e76aecc7b?w=120&auto=format&fit=crop"),
        ]

        for name, ref, barcode, cat_id, uom_id, price, cost, tax, img_url in products_data:
            prod = Product(
                company_id=company.id,
                category_id=cat_id,
                uom_id=uom_id,
                name=name,
                internal_ref=ref,
                barcode=barcode,
                product_type=ProductType.PRODUCT,
                sale_price=price,
                cost_price=cost,
                tax_rate=tax,
                includes_tax=True,
                min_stock=10,
                is_active=True,
                is_published=True,
                can_be_sold=True,
                can_be_purchased=True,
                image_url=img_url,
            )
            db.add(prod)
        await db.flush()

        print("📊 Creando stock inicial...")
        # Obtener todos los productos
        result = await db.execute(select(Product).where(Product.company_id == company.id))
        all_products = result.scalars().all()

        stock_quantities = [50, 80, 60, 40, 35, 100, 200, 500, 20, 120]
        for product, qty in zip(all_products, stock_quantities):
            quant = StockQuant(
                product_id=product.id,
                warehouse_id=warehouse_main.id,
                qty_on_hand=qty,
                qty_reserved=0,
                avg_cost=product.cost_price,
            )
            db.add(quant)

            # Kardex inicial
            move = StockMove(
                warehouse_id=warehouse_main.id,
                product_id=product.id,
                move_type=StockMoveType.INITIAL,
                reference="INVENTARIO INICIAL",
                qty=qty,
                qty_before=0,
                qty_after=qty,
                unit_cost=product.cost_price,
                total_cost=qty * product.cost_price,
            )
            db.add(move)

        print("📅 Creando lotes de ejemplo próximos a vencer (Abarrotería/Farmacia)...")
        from app.models.inventory import StockLot
        from datetime import datetime, timedelta
        
        leche = next((p for p in all_products if p.internal_ref == "LEC001"), None)
        if leche:
            lot1 = StockLot(
                product_id=leche.id,
                warehouse_id=warehouse_main.id,
                lot_number="LOTE-LECHE-2026",
                qty_on_hand=20.0,
                expiry_date=datetime.now() + timedelta(days=5),
            )
            db.add(lot1)

        paracetamol = next((p for p in all_products if p.internal_ref == "PAR001"), None)
        if paracetamol:
            lot2 = StockLot(
                product_id=paracetamol.id,
                warehouse_id=warehouse_main.id,
                lot_number="LOTE-PARA-45",
                qty_on_hand=50.0,
                expiry_date=datetime.now() + timedelta(days=12),
            )
            db.add(lot2)

        print("👥 Creando clientes de ejemplo...")
        customers = [
            ("José Pérez", "12345678", "jose.perez@gmail.com"),
            ("Ana García", "87654321", "ana.garcia@hotmail.com"),
            ("Distribuidora XYZ S.A.", "45678901", "compras@distxyz.com"),
        ]
        for name, nit, email in customers:
            partner = Partner(
                company_id=company.id,
                partner_type=PartnerType.CUSTOMER,
                name=name,
                nit=nit,
                email=email,
                is_company=False if name.count(' ') < 2 else True,
            )
            db.add(partner)

        print("🏭 Creando proveedores de ejemplo...")
        suppliers = [
            ("Distribuidora Central S.A.", "11112222", "compras@distcentral.com"),
            ("Importadora Nacional", "33334444", "ventas@importnac.com"),
            ("Laboratorios Farma GT", "55556666", "pedidos@farmalab.com"),
        ]
        for name, nit, email in suppliers:
            partner = Partner(
                company_id=company.id,
                partner_type=PartnerType.SUPPLIER,
                name=name,
                nit=nit,
                email=email,
                is_company=True,
            )
            db.add(partner)

        await db.commit()
        print("\n✅ ¡Datos iniciales creados exitosamente!")
        print(f"\n📋 Credenciales de acceso:")
        print(f"   🔑 Admin: {settings.INIT_ADMIN_EMAIL} / {settings.INIT_ADMIN_PASSWORD}")
        print(f"   👤 Cajero: cajero@empresa.com / Cajero2024!")
        print(f"\n🌐 Acceder en: http://localhost:8000")
        print(f"📚 API Docs: http://localhost:8000/api/docs")


if __name__ == "__main__":
    asyncio.run(seed())
