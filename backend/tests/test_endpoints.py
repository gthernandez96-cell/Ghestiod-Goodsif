"""Pruebas de integración para endpoints API v1."""
import pytest
import asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from app.main import app
from app.core.database import Base, get_db
from app.core.security import create_access_token, get_password_hash
from app.models import Company, User, Role, Branch, Warehouse
import uuid

client = TestClient(app)

COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

token = create_access_token(data={"sub": str(USER_ID), "email": "test_admin@empresa.com", "company_id": str(COMPANY_ID)})
headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Crear tablas y usuario inicial para los tests."""
    from app.core.database import engine

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            comp_existing = await session.execute(select(Company).where(Company.id == COMPANY_ID))
            if not comp_existing.scalar_one_or_none():
                comp = Company(id=COMPANY_ID, name="Empresa Test API", nit="999888777")
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

            existing = await session.execute(select(User).where(User.id == USER_ID))
            if not existing.scalar_one_or_none():
                user = User(
                    id=USER_ID,
                    company_id=COMPANY_ID,
                    current_branch_id=branch.id if branch else None,
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


def test_products_api():
    # 1. Crear categoría
    cat_res = client.post("/api/v1/products/categories", json={"name": "Bebidas Test", "code": "TEST-CAT"}, headers=headers)
    assert cat_res.status_code == 200
    cat_id = cat_res.json()["id"]

    # 2. Crear producto
    prod_res = client.post("/api/v1/products/", json={
        "name": "Coca Cola 355ml Test",
        "category_id": cat_id,
        "sale_price": 7.50,
        "cost_price": 4.50,
        "min_stock": 10
    }, headers=headers)
    assert prod_res.status_code == 200
    prod_id = prod_res.json()["id"]

    # 3. Modificar producto
    update_res = client.put(f"/api/v1/products/{prod_id}", json={
        "name": "Coca Cola 355ml Test Editado",
        "internal_ref": "SKU-EDITED",
        "barcode": "1234567890",
        "category_id": "",
        "sale_price": 8.00,
        "cost_price": 5.00,
        "min_stock": 15,
        "stock": 100,
        "image_url": ""
    }, headers=headers)
    assert update_res.status_code == 200

    # 4. Listar productos
    list_res = client.get("/api/v1/products/?search=Test", headers=headers)
    assert list_res.status_code == 200
    assert len(list_res.json()["items"]) >= 1


def test_partners_api():
    # Crear cliente
    res = client.post("/api/v1/partners/", json={
        "name": "Cliente de Prueba API",
        "nit": "123456-7",
        "partner_type": "customer",
        "phone": "5555-1234"
    }, headers=headers)
    assert res.status_code == 200

    # Buscar por NIT
    lookup = client.get("/api/v1/partners/lookup-nit/123456-7", headers=headers)
    assert lookup.status_code == 200
    assert lookup.json()["found"] is True


def test_dashboard_api():
    for p in ["today", "week", "month", "year"]:
        kpis = client.get(f"/api/v1/dashboard/kpis?period={p}", headers=headers)
        assert kpis.status_code == 200
        data = kpis.json()
        assert "sales_today" in data
        assert "low_stock" in data
        assert "period" in data

    custom_kpis = client.get("/api/v1/dashboard/kpis?period=custom&start_date=2026-09-01&end_date=2026-09-08", headers=headers)
    assert custom_kpis.status_code == 200

    # Sales chart test with days, period=today, period=week, period=year, period=custom
    for p in ["today", "week", "month", "year"]:
        chart = client.get(f"/api/v1/dashboard/sales-chart?period={p}", headers=headers)
        assert chart.status_code == 200
        chart_data = chart.json()
        assert "labels" in chart_data
        assert "data" in chart_data

    chart_days = client.get("/api/v1/dashboard/sales-chart?days=7", headers=headers)
    assert chart_days.status_code == 200
    assert len(chart_days.json()["labels"]) == 7

    # Breakdown endpoints with period
    cat = client.get("/api/v1/dashboard/category-breakdown?period=week", headers=headers)
    assert cat.status_code == 200
    assert "labels" in cat.json()

    pm = client.get("/api/v1/dashboard/payment-methods?period=week", headers=headers)
    assert pm.status_code == 200
    assert "labels" in pm.json()

    hours = client.get("/api/v1/dashboard/sales-by-hour?period=today", headers=headers)
    assert hours.status_code == 200
    assert "labels" in hours.json()

    prof = client.get("/api/v1/dashboard/profitability-and-runway?period=month", headers=headers)
    assert prof.status_code == 200


def test_ticket_and_zreport():
    test_uuid = str(uuid.uuid4())
    ticket_res = client.get(f"/sales/{test_uuid}/ticket")
    assert ticket_res.status_code == 200
    assert "DOCUMENTO TRIBUTARIO ELECTRÓNICO" in ticket_res.text


def test_barcode_label():
    prod_res = client.post("/api/v1/products/", json={"name": "Producto Barcode Test", "sale_price": 10.0}, headers=headers)
    assert prod_res.status_code == 200
    p_id = prod_res.json()["id"]

    label_res = client.get(f"/api/v1/products/{p_id}/barcode-label", headers=headers)
    assert label_res.status_code == 200
    assert "Etiqueta" in label_res.text


def test_customer_credit_and_payment():
    # 1. Crear cliente con crédito de Q 1000
    create_res = client.post("/api/v1/partners/", json={
        "name": "Cliente Crédito Test",
        "nit": "9876543-2",
        "phone": "5555-8888",
        "credit_limit": 1000.0,
        "payment_days": 30
    }, headers=headers)
    assert create_res.status_code == 200
    partner_id = create_res.json()["id"]

    # 2. Consultar créditos
    credits_res = client.get("/api/v1/partners/credits", headers=headers)
    assert credits_res.status_code == 200

    # 3. Registrar abono
    pay_res = client.post(f"/api/v1/partners/{partner_id}/credit-payments", json={
        "amount": 200.0,
        "payment_method": "cash",
        "reference": "Abono inicial"
    }, headers=headers)
    assert pay_res.status_code == 200
    assert pay_res.json()["amount_paid"] == 200.0


def test_pos_cashier_flow():
    # 0. Limpiar sesiones abiertas previas si existen
    act_chk = client.get("/api/v1/cashier/sessions/active", headers=headers)
    if act_chk.status_code == 200 and act_chk.json().get("has_active_session"):
        old_id = act_chk.json()["session"]["id"]
        client.post(f"/api/v1/cashier/sessions/{old_id}/close", json={"closing_amount_real": 0.0}, headers=headers)

    # 1. Apertura de caja
    open_res = client.post("/api/v1/cashier/open", json={
        "branch_id": str(COMPANY_ID),
        "opening_amount": 500.0,
        "notes": "Apertura inicial test"
    }, headers=headers)
    assert open_res.status_code == 200
    sess_data = open_res.json()
    sess_id = sess_data["id"]

    # 2. Consultar sesión activa
    act_res = client.get("/api/v1/cashier/sessions/active", headers=headers)
    assert act_res.status_code == 200
    assert act_res.json()["has_active_session"] is True
    assert act_res.json()["session"]["opening_balance"] == 500.0

    # 3. Realizar una venta vinculada a la sesión
    sale_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "cashier_session_id": sess_id,
        "customer_nit": "CF",
        "customer_name": "CONSUMIDOR FINAL",
        "sale_type": "pos",
        "generate_fel": True,
        "lines": [{
            "product_id": str(uuid.uuid4()),
            "product_name": "Gaseosa Test 500ml",
            "qty": 2,
            "unit_price": 10.0,
            "cost_price": 5.0
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 20.0
        }]
    }, headers=headers)
    assert sale_res.status_code == 200
    sale_data = sale_res.json()
    assert sale_data["total"] == 20.0
    assert sale_data["fel_uuid"] is not None

    # 4. Verificar que se actualizó el total de ventas en la sesión
    act_res2 = client.get("/api/v1/cashier/sessions/active", headers=headers)
    assert act_res2.json()["session"]["total_sales"] == 20.0
    assert act_res2.json()["session"]["expected_cash"] == 520.0

    # 5. Cierre de caja y Z-Report
    close_res = client.post(f"/api/v1/cashier/sessions/{sess_id}/close", json={
        "closing_amount_real": 520.0,
        "notes": "Cierre perfecto"
    }, headers=headers)
    assert close_res.status_code == 200
    assert close_res.json()["closing_difference"] == 0.0

    # 6. Renderizado del reporte Z
    z_res = client.get(f"/cashier/sessions/{sess_id}/z-report")
    assert z_res.status_code == 200
    assert "REPORTE DE CIERRE Z" in z_res.text


def test_partners_360_and_kpis():
    """Validar KPIs de cartera y expediente 360 del cliente."""
    # 1. Validar KPIs de cartera
    kpis_res = client.get("/api/v1/partners/kpis", headers=headers)
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert "total_contacts" in kpis
    assert "total_debt" in kpis
    assert "total_credit_limit" in kpis
    assert "total_available_credit" in kpis

    # 2. Crear cliente para prueba 360
    create_res = client.post("/api/v1/partners/", json={
        "name": "Cliente CRM 360 Test",
        "trade_name": "Tienda El Sol",
        "nit": "8877665-4",
        "cui": "2589631470101",
        "partner_type": "customer",
        "phone": "5555-4321",
        "mobile": "4444-1234",
        "email": "crm@cliente.com",
        "address": "Zona 14, La Cañada",
        "city": "Guatemala",
        "country": "Guatemala",
        "is_company": True,
        "credit_status": "active",
        "credit_limit": 5000.0,
        "payment_days": 30,
        "notes": "Cliente corporativo preferente",
        "tags": ["VIP", "Mayorista"]
    }, headers=headers)
    assert create_res.status_code == 200
    p_id = create_res.json()["id"]

    # 3. Consultar detalle 360
    detail_res = client.get(f"/api/v1/partners/{p_id}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["name"] == "Cliente CRM 360 Test"
    assert detail["credit_limit"] == 5000.0
    assert detail["is_company"] is True
    assert "VIP" in detail["tags"]

    # 4. Consultar historial de ventas del cliente
    sales_res = client.get(f"/api/v1/partners/{p_id}/sales", headers=headers)
    assert sales_res.status_code == 200
    assert "items" in sales_res.json()

    # 5. Consultar estado de cuenta
    stmt_res = client.get(f"/api/v1/partners/{p_id}/statement", headers=headers)
    assert stmt_res.status_code == 200
    stmt_data = stmt_res.json()
    assert "customer" in stmt_data
    assert "summary" in stmt_data


def test_products_360_kpis_and_kardex():
    """Validar KPIs de inventario, detalle 360, ajuste de stock y Kardex."""
    # 1. Validar KPIs de inventario
    kpis_res = client.get("/api/v1/products/kpis", headers=headers)
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert "total_products" in kpis
    assert "total_valuation" in kpis
    assert "low_stock_count" in kpis
    assert "out_of_stock_count" in kpis

    # 2. Crear producto para prueba
    create_res = client.post("/api/v1/products/", json={
        "name": "Martillo Profesional 16oz",
        "internal_ref": "HER-001",
        "barcode": "740123456789",
        "sale_price": 75.0,
        "cost_price": 45.0,
        "min_price": 65.0,
        "min_stock": 5.0,
        "max_stock": 50.0,
        "tax_rate": 12.0,
        "product_type": "product",
        "tracking": "none"
    }, headers=headers)
    assert create_res.status_code == 200
    prod_id = create_res.json()["id"]

    # 3. Consultar detalle 360 del producto
    detail_res = client.get(f"/api/v1/products/{prod_id}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["name"] == "Martillo Profesional 16oz"
    assert detail["sale_price"] == 75.0
    assert detail["cost_price"] == 45.0
    assert detail["min_stock"] == 5.0

    # 4. Ajustar stock del producto
    adj_res = client.post(f"/api/v1/products/{prod_id}/adjust-stock", json={
        "new_qty": 25.0,
        "reason": "conteo_fisico",
        "notes": "Auditoría inicial de bodega"
    }, headers=headers)
    assert adj_res.status_code == 200
    assert adj_res.json()["new_qty"] == 25.0

    # 5. Consultar Kardex del producto
    kardex_res = client.get(f"/api/v1/products/{prod_id}/kardex", headers=headers)
    assert kardex_res.status_code == 200
    kardex_items = kardex_res.json()["items"]
    assert len(kardex_items) >= 1
    assert kardex_items[0]["qty"] == 25.0

    # 6. Probar subida de imagen desde ordenador
    import io
    fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")
    upload_res = client.post(
        "/api/v1/products/upload-image",
        files={"file": ("test_product.png", fake_image, "image/png")},
        headers=headers
    )
    assert upload_res.status_code == 200
    assert "url" in upload_res.json()
    assert upload_res.json()["url"].startswith("/static/uploads/products/")


def test_purchases_360_kpis_and_receive():
    """Validar KPIs de compras, creación de orden, detalle y recepción en bodega."""
    # 1. Validar KPIs de compras
    kpis_res = client.get("/api/v1/purchases/kpis", headers=headers)
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert "total_spend" in kpis
    assert "pending_reception_count" in kpis
    assert "active_suppliers_count" in kpis

    # 2. Crear proveedor
    supp_res = client.post("/api/v1/partners/", json={
        "name": "Distribuidora La Ceiba S.A.",
        "partner_type": "supplier",
        "nit": "9988776-5",
        "phone": "2233-4455",
        "email": "ventas@laceiba.com"
    }, headers=headers)
    assert supp_res.status_code == 200
    supplier_id = supp_res.json()["id"]

    # 3. Crear producto asignado a este proveedor
    prod_res = client.post("/api/v1/products/", json={
        "name": "Aceite Vegetal 800ml Compra Test",
        "sale_price": 20.0,
        "cost_price": 14.0,
        "product_type": "product",
        "supplier_id": supplier_id
    }, headers=headers)
    assert prod_res.status_code == 200
    product_id = prod_res.json()["id"]

    # 3.1 Consultar productos filtrados por proveedor
    supp_prods_res = client.get(f"/api/v1/products/?supplier_id={supplier_id}", headers=headers)
    assert supp_prods_res.status_code == 200
    supp_prods = supp_prods_res.json()["items"]
    assert len(supp_prods) >= 1
    assert supp_prods[0]["supplier_id"] == supplier_id

    # 4. Crear orden de compra
    po_res = client.post("/api/v1/purchases/", json={
        "supplier_id": supplier_id,
        "supplier_ref": "FAC-TEST-001",
        "notes": "Entrega urgente en bodega central",
        "lines": [
            {
                "product_id": product_id,
                "product_name": "Aceite Vegetal 800ml Compra Test",
                "qty": 50.0,
                "unit_price": 14.0,
                "tax_rate": 12.0
            }
        ]
    }, headers=headers)
    assert po_res.status_code == 200
    po_data = po_res.json()
    assert "id" in po_data
    po_id = po_data["id"]

    # 5. Consultar detalle de la orden de compra
    detail_res = client.get(f"/api/v1/purchases/{po_id}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["supplier_id"] == supplier_id
    assert len(detail["lines"]) == 1
    assert detail["lines"][0]["qty_ordered"] == 50.0

    # 6. Recibir mercadería en bodega
    recv_res = client.post(f"/api/v1/purchases/{po_id}/receive", json={}, headers=headers)
    assert recv_res.status_code == 200
    assert recv_res.json()["state"] == "received"

    # 7. Verificar que el Kardex del producto tenga el movimiento de compra
    kardex_res = client.get(f"/api/v1/products/{product_id}/kardex", headers=headers)
    assert kardex_res.status_code == 200
    kardex_items = kardex_res.json()["items"]
    assert any(k["move_type"] == "purchase" for k in kardex_items)


def test_multi_company_and_warehouses():
    """Test para listar, crear empresas y gestionar bodegas."""
    # 1. Listar empresas
    comp_list = client.get("/api/v1/companies", headers=headers)
    assert comp_list.status_code == 200
    assert len(comp_list.json()) >= 1

    # 2. Crear una nueva empresa secundaria con NIT único
    unique_nit = f"NIT-{uuid.uuid4().hex[:6]}"
    new_comp = client.post("/api/v1/companies", json={
        "name": "Distribuidora del Sur S.A.",
        "trade_name": "Sur Distribuciones",
        "nit": unique_nit,
        "tax_regime": "general",
        "city": "Quetzaltenango"
    }, headers=headers)
    assert new_comp.status_code == 200
    new_comp_id = new_comp.json()["id"]

    # 3. Listar bodegas de la empresa activa
    wh_list = client.get("/api/v1/branches/warehouses", headers=headers)
    assert wh_list.status_code == 200
    warehouses = wh_list.json()
    assert len(warehouses) >= 1

    # 4. Crear una nueva bodega
    new_wh = client.post("/api/v1/branches/warehouses", json={
        "name": "Bodega Refrigeración 01",
        "code": "BOD-REF-01",
        "is_default": False
    }, headers=headers)
    assert new_wh.status_code == 200
    assert "id" in new_wh.json()


def test_transfers_360_flow():
    """Test para el ciclo completo de traslados entre bodegas con KPIs y Kardex."""

    # 1. Obtener bodegas disponibles
    wh_res = client.get("/api/v1/branches/warehouses", headers=headers)
    assert wh_res.status_code == 200
    warehouses = wh_res.json()
    assert len(warehouses) >= 1
    orig_wh_id = warehouses[0]["id"]

    # Crear una segunda bodega de destino para el traslado
    dw_res = client.post("/api/v1/branches/warehouses", json={
        "name": "Bodega Destino Test",
        "code": "BOD-DEST-TEST"
    }, headers=headers)
    assert dw_res.status_code == 200
    dest_wh_id = dw_res.json()["id"]

    # 2. Crear producto para el traslado
    prod_res = client.post("/api/v1/products/", json={
        "name": "Leche Entera 1L Traslado Test",
        "sale_price": 12.0,
        "cost_price": 8.0,
        "product_type": "product"
    }, headers=headers)
    assert prod_res.status_code == 200
    prod_id = prod_res.json()["id"]

    # Ajustar existencia en bodega origen (+20 unidades)
    adj_res = client.post(f"/api/v1/products/{prod_id}/adjust-stock", json={
        "new_qty": 20.0,
        "warehouse_id": orig_wh_id,
        "reason": "Inventario Inicial Origen"
    }, headers=headers)
    assert adj_res.status_code == 200

    # 3. Crear solicitud de traslado
    tr_res = client.post("/api/v1/transfers/", json={
        "origin_warehouse_id": orig_wh_id,
        "dest_warehouse_id": dest_wh_id,
        "notes": "Envío de reposición",
        "lines": [
            {
                "product_id": prod_id,
                "product_name": "Leche Entera 1L Traslado Test",
                "qty": 5.0
            }
        ]
    }, headers=headers)
    assert tr_res.status_code == 200
    tr_id = tr_res.json()["id"]

    # 4. Consultar KPIs de traslados
    kpis_res = client.get("/api/v1/transfers/kpis", headers=headers)
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert kpis["total_transfers"] >= 1

    # 5. Despachar mercadería de origen
    send_res = client.post(f"/api/v1/transfers/{tr_id}/send", headers=headers)
    assert send_res.status_code == 200
    assert send_res.json()["state"] == "sent"

    # 6. Confirmar recepción en destino
    receive_res = client.post(f"/api/v1/transfers/{tr_id}/receive", headers=headers)
    assert receive_res.status_code == 200
    assert receive_res.json()["state"] == "received"

    # 7. Verificar movimientos en el Kardex
    kardex_res = client.get(f"/api/v1/products/{prod_id}/kardex", headers=headers)
    assert kardex_res.status_code == 200
    k_items = kardex_res.json()["items"]
    assert any(k["move_type"] == "transfer_out" for k in k_items)
    assert any(k["move_type"] == "transfer_in" for k in k_items)


def test_reports_360_suite():
    """Test para todos los endpoints de Business Intelligence y Reportes 360."""
    # 1. Executive KPIs
    kpi_res = client.get("/api/v1/reports/executive-kpis", headers=headers)
    assert kpi_res.status_code == 200
    kpis = kpi_res.json()
    assert "total_sales" in kpis
    assert "inventory_valuation" in kpis

    # 2. Ventas por período
    sales_res = client.get("/api/v1/reports/sales-by-period", headers=headers)
    assert sales_res.status_code == 200
    assert isinstance(sales_res.json(), list)

    # 3. Top productos
    top_res = client.get("/api/v1/reports/top-products", headers=headers)
    assert top_res.status_code == 200
    assert isinstance(top_res.json(), list)

    # 4. Valoración de inventario por bodega
    inv_res = client.get("/api/v1/reports/inventory-valuation", headers=headers)
    assert inv_res.status_code == 200
    assert isinstance(inv_res.json(), list)

    # 5. Métodos de pago
    pay_res = client.get("/api/v1/reports/payment-methods", headers=headers)
    assert pay_res.status_code == 200
    assert isinstance(pay_res.json(), list)

    # 6. Libro oficial de ventas FEL SAT
    fel_res = client.get("/api/v1/reports/fel-ledger", headers=headers)
    assert fel_res.status_code == 200
    assert isinstance(fel_res.json(), list)


def test_quotes_and_ap_flow():
    """Test para flujo de Cotizaciones (creación, cálculo y conversión a venta) y Cuentas por Pagar AP."""
    # 1. Crear un producto de prueba
    p_res = client.post("/api/v1/products/", json={
        "name": "Producto Cotización Test",
        "sale_price": 100.0,
        "cost_price": 60.0,
        "stock": 25.0
    }, headers=headers)
    assert p_res.status_code == 200
    prod_id = p_res.json()["id"]

    # 2. Crear una cotización con descuento
    q_res = client.post("/api/v1/quotes/", json={
        "customer_name": "Corporación ABC S.A.",
        "customer_nit": "1234567-8",
        "valid_days": 30,
        "lines": [
            {
                "product_id": prod_id,
                "product_name": "Producto Cotización Test",
                "qty": 2.0,
                "unit_price": 100.0,
                "discount_pct": 10.0  # 200 - 10% = 180 total
            }
        ]
    }, headers=headers)
    assert q_res.status_code == 200
    q_id = q_res.json()["id"]
    assert q_res.json()["total"] == 180.0

    # 3. Consultar detalle de cotización
    q_detail = client.get(f"/api/v1/quotes/{q_id}", headers=headers)
    assert q_detail.status_code == 200
    assert len(q_detail.json()["lines"]) == 1

    # 4. Convertir cotización a venta
    conv_res = client.post(f"/api/v1/quotes/{q_id}/convert", headers=headers)
    assert conv_res.status_code == 200
    assert conv_res.json()["status"] == "converted"

    # 5. Cuentas por Pagar (AP): Crear factura de proveedor
    bill_res = client.post("/api/v1/ap/bills", json={
        "supplier_name": "Distribuidora Mayorista Test",
        "supplier_nit": "9876543-2",
        "number": f"FAC-TEST-{uuid.uuid4().hex[:6]}",
        "credit_days": 30,
        "total": 500.0
    }, headers=headers)
    assert bill_res.status_code == 200
    bill_id = bill_res.json()["id"]
    assert bill_res.json()["balance"] == 500.0

    # 6. Registrar abono parcial de Q200
    pay_res = client.post(f"/api/v1/ap/bills/{bill_id}/payments", json={
        "amount": 200.0,
        "payment_method": "transfer",
        "reference": "TRANSF-001"
    }, headers=headers)
    assert pay_res.status_code == 200
    assert pay_res.json()["new_balance"] == 300.0

    # 7. Consultar detalle con historial de abonos
    bill_detail = client.get(f"/api/v1/ap/bills/{bill_id}", headers=headers)
    assert bill_detail.status_code == 200
    assert bill_detail.json()["balance"] == 300.0
    assert len(bill_detail.json()["payments"]) == 1

    # 8. Consultar KPIs de AP
    kpi_res = client.get("/api/v1/ap/kpis", headers=headers)
    assert kpi_res.status_code == 200
    assert kpi_res.json()["total_debt"] >= 300.0


def test_accounting_360_suite():
    """Test para módulo de Contabilidad y Finanzas 360: Catálogo de Cuentas, Partida Doble y Balances."""
    # 1. Consultar Catálogo de Cuentas
    acc_res = client.get("/api/v1/accounting/accounts", headers=headers)
    assert acc_res.status_code == 200
    accounts = acc_res.json()
    assert len(accounts) >= 5

    # 2. Crear una nueva cuenta contable personalizada
    custom_code = f"99{uuid.uuid4().hex[:6]}"
    new_acc_res = client.post("/api/v1/accounting/accounts", json={
        "code": custom_code,
        "name": "Gastos de Capacitación",
        "account_type": "expense"
    }, headers=headers)
    assert new_acc_res.status_code == 200

    # 3. Intentar crear asiento descuadrado (debe fallar con 400)
    unbalanced_res = client.post("/api/v1/accounting/entries", json={
        "concept": "Asiento Descuadrado Inválido",
        "lines": [
            {"account_id": "1101", "debit": 100.0, "credit": 0.0},
            {"account_id": "4101", "debit": 0.0, "credit": 50.0}
        ]
    }, headers=headers)
    assert unbalanced_res.status_code == 400

    # 4. Crear asiento contable balanceado (Partida Doble)
    balanced_res = client.post("/api/v1/accounting/entries", json={
        "concept": "Pago de Alquiler de Bodega",
        "journal": "Gastos Operativos",
        "lines": [
            {"account_id": "6102", "debit": 2500.0, "credit": 0.0},
            {"account_id": "1102", "debit": 0.0, "credit": 2500.0}
        ]
    }, headers=headers)
    assert balanced_res.status_code == 200
    assert balanced_res.json()["debit"] == 2500.0
    assert balanced_res.json()["credit"] == 2500.0

    # 5. Consultar Balance de Comprobación (Trial Balance)
    trial_res = client.get("/api/v1/accounting/trial-balance", headers=headers)
    assert trial_res.status_code == 200
    trial = trial_res.json()
    assert "totals" in trial
    assert trial["totals"]["is_balanced"] is True

    # 6. Consultar Estado de Resultados (P&L)
    pnl_res = client.get("/api/v1/accounting/income-statement", headers=headers)
    assert pnl_res.status_code == 200
    pnl = pnl_res.json()
    assert "gross_profit" in pnl
    assert "net_profit" in pnl


def test_settings_and_backup_suite():
    """Test para módulo de Configuraciones, datos fiscales de empresa, usuarios y backup."""
    # 1. Obtener ajustes de la empresa activa
    set_res = client.get("/api/v1/companies/current", headers=headers)
    assert set_res.status_code == 200
    settings = set_res.json()
    assert "trade_name" in settings
    assert "nit" in settings
    assert "pos_ticket_format" in settings

    # 2. Actualizar configuración de empresa
    upd_res = client.put("/api/v1/companies/current", json={
        "trade_name": "Corporación Demo Express",
        "pos_ticket_format": "80mm",
        "ticket_footer": "¡Gracias por su compra en nuestra tienda!",
        "fel_provider": "INFILE"
    }, headers=headers)
    assert upd_res.status_code == 200
    assert upd_res.json()["status"] == "saved"

    # 3. Listar usuarios del sistema
    users_res = client.get("/api/v1/companies/users", headers=headers)
    assert users_res.status_code == 200
    users = users_res.json()
    assert len(users) >= 1

    # 4. Descargar backup de base de datos
    backup_res = client.get("/api/v1/companies/backup/download", headers=headers)
    assert backup_res.status_code == 200
    assert len(backup_res.content) > 0


def test_advanced_settings_and_user_creation():
    """Test para creación de usuarios colaboradores y políticas avanzadas de inventario / pagos."""
    # 1. Crear nuevo usuario colaborador
    unique_user = f"user_{uuid.uuid4().hex[:6]}"
    u_res = client.post("/api/v1/companies/users", json={
        "full_name": "Colaborador Demo",
        "email": f"{unique_user}@empresa.com",
        "username": unique_user,
        "password": "Password123!",
        "is_superuser": False
    }, headers=headers)
    assert u_res.status_code == 200
    u_id = u_res.json()["id"]

    # 2. Alternar estado activo / bloqueado
    toggle_res = client.post(f"/api/v1/companies/users/{u_id}/toggle-status", headers=headers)
    assert toggle_res.status_code == 200
    assert toggle_res.json()["is_active"] is False

    # Reactivar
    toggle_back = client.post(f"/api/v1/companies/users/{u_id}/toggle-status", headers=headers)
    assert toggle_back.status_code == 200
    assert toggle_back.json()["is_active"] is True

    # 3. Guardar políticas avanzadas de inventario y doble moneda
    adv_upd = client.put("/api/v1/companies/current", json={
        "inventory_valuation_method": "fifo",
        "low_stock_threshold": 10,
        "enable_usd_currency": True,
        "usd_exchange_rate": 7.80,
        "payment_credit": True
    }, headers=headers)
    assert adv_upd.status_code == 200

    # 4. Verificar persistencia de ajustes
    get_adv = client.get("/api/v1/companies/current", headers=headers)
    assert get_adv.status_code == 200
    assert get_adv.json()["inventory_valuation_method"] == "fifo"
    assert get_adv.json()["low_stock_threshold"] == 10
    assert get_adv.json()["enable_usd_currency"] is True
    assert get_adv.json()["usd_exchange_rate"] == 7.80


def test_pos_sessions_and_cash_control_suite():
    """Test para módulo de Puntos de Venta (Terminales), Control de Efectivo y Órdenes de Sesión."""
    # 1. Consultar estado en vivo de terminales
    term_res = client.get("/api/v1/cashier/terminals", headers=headers)
    assert term_res.status_code == 200
    terminals = term_res.json()
    assert isinstance(terminals, list)

    # 2. Abrir una sesión de caja
    b_res = client.get("/api/v1/branches/", headers=headers)
    assert b_res.status_code == 200
    branch_id = b_res.json()[0]["id"]

    open_res = client.post("/api/v1/cashier/open", json={
        "branch_id": branch_id,
        "opening_amount": 500.0,
        "notes": "Apertura turno matutino"
    }, headers=headers)
    assert open_res.status_code == 200
    sess_id = open_res.json()["id"]

    # 3. Registrar salida de efectivo (Cash Out)
    cash_out_res = client.post("/api/v1/cashier/movements/", json={
        "session_id": sess_id,
        "move_type": "cash_out",
        "amount": 50.0,
        "reason": "Compra de insumos de limpieza"
    }, headers=headers)
    assert cash_out_res.status_code == 200
    assert cash_out_res.json()["amount"] == 50.0

    # 4. Consultar movimientos de la sesión
    moves_res = client.get(f"/api/v1/cashier/sessions/{sess_id}/movements", headers=headers)
    assert moves_res.status_code == 200
    assert len(moves_res.json()) >= 1

    # 5. Consultar órdenes de la sesión
    orders_res = client.get(f"/api/v1/cashier/sessions/{sess_id}/orders", headers=headers)
    assert orders_res.status_code == 200
    assert isinstance(orders_res.json(), list)

    # 6. Crear una venta al crédito y validar que NO afecte el efectivo esperado
    prod_res = client.get("/api/v1/products/", headers=headers)
    assert prod_res.status_code == 200
    if not prod_res.json()["items"]:
        p_res = client.post("/api/v1/products/", json={"name": "Prod Test POS", "sale_price": 50.0}, headers=headers)
        prod_id = p_res.json()["id"]
    else:
        prod_id = prod_res.json()["items"][0]["id"]

    unique_cust = client.post("/api/v1/partners/", json={
        "name": "Cliente Crédito POS Test",
        "partner_type": "customer",
        "nit": f"NIT-{uuid.uuid4().hex[:6]}",
        "credit_limit": 5000.0,
        "payment_days": 30
    }, headers=headers)
    assert unique_cust.status_code == 200
    cust_id = unique_cust.json()["id"]

    wh_res = client.get("/api/v1/warehouses/", headers=headers)
    wh_id = wh_res.json()[0]["id"] if wh_res.status_code == 200 and wh_res.json() else branch_id

    credit_sale = client.post("/api/v1/sales/", json={
        "customer_id": cust_id,
        "branch_id": branch_id,
        "warehouse_id": wh_id,
        "cashier_session_id": sess_id,
        "sale_type": "credit",
        "lines": [{
            "product_id": prod_id,
            "product_name": "Producto Prueba Crédito",
            "qty": 2.0,
            "unit_price": 75.0,
        }],
        "payments": [{
            "payment_method": "credit",
            "amount": 150.0
        }]
    }, headers=headers)
    assert credit_sale.status_code == 200

    # 7. Consultar sesión activa y verificar que el crédito esté separado del efectivo
    active_sess = client.get("/api/v1/cashier/sessions/active", headers=headers)
    assert active_sess.status_code == 200
    sess_data = active_sess.json()["session"]
    assert sess_data["total_credit_sales"] >= 150.0
    # El efectivo esperado DEBE SER: apertura (500) - salida (50) = 450 (sin sumar los 150 de crédito)
    assert sess_data["expected_cash"] == (500.0 - 50.0 + sess_data["total_cash_sales"])


def test_loyalty_delivery_labels_and_catalog():
    """Test para Programa de Lealtad, Despachos / Delivery, Etiquetas y Catálogo QR."""
    # 1. Habilitar y configurar Programa de Lealtad
    loyalty_upd = client.put("/api/v1/companies/current", json={
        "enable_loyalty_program": True,
        "loyalty_points_per_quetzal": 2.0,
        "loyalty_redeem_rate": 15.0
    }, headers=headers)
    assert loyalty_upd.status_code == 200

    comp_get = client.get("/api/v1/companies/current", headers=headers)
    assert comp_get.status_code == 200
    assert comp_get.json()["enable_loyalty_program"] is True
    assert comp_get.json()["loyalty_points_per_quetzal"] == 2.0
    assert comp_get.json()["loyalty_redeem_rate"] == 15.0

    # 2. Deshabilitar Programa de Lealtad
    loyalty_off = client.put("/api/v1/companies/current", json={
        "enable_loyalty_program": False
    }, headers=headers)
    assert loyalty_off.status_code == 200
    comp_off = client.get("/api/v1/companies/current", headers=headers)
    assert comp_off.json()["enable_loyalty_program"] is False

    # 3. Crear y avanzar estado de orden de Delivery
    del_res = client.post("/api/v1/delivery/orders", json={
        "customer_name": "Cliente Delivery Test",
        "customer_phone": "+502 5555-6666",
        "delivery_address": "Zona 14 Ciudad de Guatemala",
        "delivery_type": "delivery",
        "total_amount": 250.0,
        "items_summary": "1x Producto Demo"
    }, headers=headers)
    assert del_res.status_code == 200
    order_id = del_res.json()["id"]

    # Avanzar a en ruta
    del_route = client.put(f"/api/v1/delivery/orders/{order_id}/status", json={
        "status": "in_route",
        "driver_name": "Motorista 1"
    }, headers=headers)
    assert del_route.status_code == 200
    assert del_route.json()["status"] == "in_route"

    # Avanzar a entregado
    del_done = client.put(f"/api/v1/delivery/orders/{order_id}/status", json={
        "status": "delivered"
    }, headers=headers)
    assert del_done.status_code == 200
    assert del_done.json()["status"] == "delivered"

    # KPIs de delivery
    kpi_res = client.get("/api/v1/delivery/kpis", headers=headers)
    assert kpi_res.status_code == 200
    assert kpi_res.json()["total_orders"] >= 1

    # 4. Validar páginas HTML
    assert client.get("/inventory/labels").status_code == 200
    assert client.get("/delivery").status_code == 200
    assert client.get("/catalog").status_code == 200


def test_csv_import_export_and_analytics_suite():
    """Pruebas para importación/exportación masiva CSV y analítica avanzada."""
    # 1. Descargar plantillas CSV
    prod_tpl = client.get("/api/v1/products/template-csv", headers=headers)
    assert prod_tpl.status_code == 200
    assert "codigo_interno" in prod_tpl.text

    part_tpl = client.get("/api/v1/partners/template-csv", headers=headers)
    assert part_tpl.status_code == 200
    assert "nombre,nit" in part_tpl.text

    # 2. Importar Productos CSV
    csv_prod_content = """codigo_interno,codigo_barras,nombre,categoria,precio_venta,precio_costo,stock_minimo,stock_inicial,unidad_medida,descripcion
PRD-CSV-01,740111222333,Aceite Premium 1L,Abarrotes,28.50,22.00,10,50,Unidad,Aceite vegetal
PRD-CSV-02,740111222334,Frijol Colorado 1lb,Granos,8.00,5.50,15,100,Libra,Frijol seleccionado
"""
    files = {"file": ("productos.csv", csv_prod_content.encode("utf-8"), "text/csv")}
    prod_imp = client.post("/api/v1/products/import-csv", files=files, headers=headers)
    assert prod_imp.status_code == 200
    assert prod_imp.json()["status"] == "success"
    assert prod_imp.json()["total_processed"] >= 2

    # 3. Exportar Catálogo Productos CSV
    prod_exp = client.get("/api/v1/products/export-csv", headers=headers)
    assert prod_exp.status_code == 200
    assert "Aceite Premium 1L" in prod_exp.text

    # 4. Importar Clientes CSV
    csv_part_content = """nombre,nit,cui,tipo,telefono,email,direccion,limite_credito,dias_credito
Cliente Corporativo S.A.,9876543-2,,customer,55554444,compras@corp.com,Zona 4 Guatemala,10000,45
Proveedor Mayorista,1122334-5,,supplier,22334455,ventas@prov.com,Carretera al Salvador,0,0
"""
    files_part = {"file": ("clientes.csv", csv_part_content.encode("utf-8"), "text/csv")}
    part_imp = client.post("/api/v1/partners/import-csv", files=files_part, headers=headers)
    assert part_imp.status_code == 200
    assert part_imp.json()["status"] == "success"
    assert part_imp.json()["total_processed"] >= 2

    # 5. Exportar Clientes CSV
    part_exp = client.get("/api/v1/partners/export-csv", headers=headers)
    assert part_exp.status_code == 200
    assert "Cliente Corporativo S.A." in part_exp.text

    # 6. Exportar Libro FEL SAT CSV
    fel_exp = client.get("/api/v1/reports/fel-ledger-csv", headers=headers)
    assert fel_exp.status_code == 200
    assert "Fecha Certificación" in fel_exp.text

    # 7. Dashboard Ventas por Hora & Alertas de Stock
    hour_res = client.get("/api/v1/dashboard/sales-by-hour", headers=headers)
    assert hour_res.status_code == 200
    assert "labels" in hour_res.json()
    assert len(hour_res.json()["labels"]) > 0

    stock_res = client.get("/api/v1/dashboard/stock-alerts", headers=headers)
    assert stock_res.status_code == 200
    assert "total_alerts" in stock_res.json()


def test_void_sale_and_profitability_runway_suite():
    """Pruebas de anulación de venta SAT (Nota de Crédito) y analítica de rentabilidad / runway."""
    # 1. Crear una venta POS
    branches = client.get("/api/v1/branches/", headers=headers).json()
    b_id = branches[0]["id"]
    warehouses = client.get("/api/v1/branches/warehouses", headers=headers).json()
    w_id = warehouses[0]["id"]
    products = client.get("/api/v1/products/", headers=headers).json()["items"]
    p_id = products[0]["id"]
    p_name = products[0]["name"]
    p_price = products[0]["sale_price"]

    sale_payload = {
        "branch_id": b_id,
        "warehouse_id": w_id,
        "customer_nit": "CF",
        "customer_name": "CONSUMIDOR FINAL",
        "sale_type": "pos",
        "lines": [
            {
                "product_id": p_id,
                "product_name": p_name,
                "qty": 2.0,
                "unit_price": p_price,
                "tax_rate": 12.0
            }
        ],
        "payments": [
            {
                "payment_method": "cash",
                "amount": p_price * 2.0
            }
        ],
        "generate_fel": False
    }
    create_res = client.post("/api/v1/sales/", json=sale_payload, headers=headers)
    assert create_res.status_code == 200
    sale_id = create_res.json()["id"]

    # 2. Anular la venta vía /void
    void_res = client.post(f"/api/v1/sales/{sale_id}/void", json={"reason": "Cliente solicitó devolución de producto"}, headers=headers)
    assert void_res.status_code == 200
    assert void_res.json()["status"] == "success"
    assert void_res.json()["stock_reversed"] == True

    # 3. Consultar métricas de rentabilidad y runway de stock
    prof_res = client.get("/api/v1/dashboard/profitability-and-runway", headers=headers)
    assert prof_res.status_code == 200
    prof_data = prof_res.json()
    assert "top_profitable_products" in prof_data
    assert "stock_runway_alerts" in prof_data
    assert "vip_customers" in prof_data


def test_kardex_stock_reconciliation():
    """Validar detección de discrepancia entre stock y kardex, y reconciliación automática."""
    # 1. Crear producto nuevo para la prueba
    sku_code = f"TEST-KDX-{uuid.uuid4().hex[:6]}"
    prod_res = client.post("/api/v1/products/", json={
        "name": "Producto Prueba Kardex",
        "internal_ref": sku_code,
        "sale_price": 50.0,
        "cost_price": 30.0,
        "track_inventory": True
    }, headers=headers)
    assert prod_res.status_code == 200
    prod_id = prod_res.json()["id"]

    # 2. Ajustar existencia vía endpoint de stock
    adj_res = client.post(f"/api/v1/products/{prod_id}/adjust-stock", json={
        "new_qty": 10.0,
        "reason": "conteo_fisico",
        "notes": "Entrada inicial para test kardex"
    }, headers=headers)
    assert adj_res.status_code == 200

    # 3. Consultar kardex y verificar balance
    kdx_res = client.get(f"/api/v1/products/{prod_id}/kardex", headers=headers)
    assert kdx_res.status_code == 200
    kdx_data = kdx_res.json()
    assert kdx_data["kardex_balance"] == 10.0
    assert kdx_data["current_stock"] == 10.0
    assert kdx_data["has_discrepancy"] is False

    # 4. Sincronizar stock con Kardex vía endpoint dedicado
    sync_res = client.post(f"/api/v1/products/{prod_id}/sync-kardex", headers=headers)
    assert sync_res.status_code == 200
    sync_data = sync_res.json()
    assert sync_data["status"] == "success"
    assert sync_data["new_stock"] == 10.0


def test_dashboard_sales_by_hour_timezone():
    """Validar que la gráfica de horas pico mapea correctamente las horas locales de Guatemala."""
    # 1. Crear una venta POS en el momento actual
    sale_payload = {
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "CONSUMIDOR FINAL",
        "sale_type": "pos",
        "lines": [
            {
                "product_id": str(uuid.uuid4()),
                "product_name": "Producto Horas Pico",
                "qty": 1,
                "unit_price": 75.50,
                "cost_price": 40.00
            }
        ],
        "payments": [
            {
                "payment_method": "cash",
                "amount": 75.50
            }
        ],
        "generate_fel": False
    }
    create_res = client.post("/api/v1/sales/", json=sale_payload, headers=headers)
    assert create_res.status_code == 200

    # 2. Consultar endpoint de Horarios Pico de Venta
    res = client.get("/api/v1/dashboard/sales-by-hour?period=today", headers=headers)
    assert res.status_code == 200
    data = res.json()

    assert "labels" in data
    assert "sales" in data
    assert "counts" in data
    assert len(data["labels"]) >= 18  # Al menos jornada estándar 06:00 a 23:00

    # 3. La suma total de ventas en el reporte de horas debe incluir la venta recién hecha
    total_hourly_sales = sum(data["sales"])
    assert total_hourly_sales >= 75.50


def test_inventory_moves_and_kardex_view():
    """Verifica que el Kardex general y por producto devuelva movimientos, balance y filtros correctamente."""
    # 1. Crear producto
    prod_sku = f"KDX-{uuid.uuid4().hex[:6]}"
    prod_payload = {
        "name": f"Producto Test Kardex {uuid.uuid4().hex[:4]}",
        "internal_ref": prod_sku,
        "sale_price": 45.0,
        "cost_price": 25.0,
        "stock": 0.0,
        "min_stock": 5.0,
        "is_active": True
    }
    create_res = client.post("/api/v1/products/", json=prod_payload, headers=headers)
    assert create_res.status_code == 200
    prod_data = create_res.json()
    prod_id = prod_data["id"]

    # 2. Registrar movimiento de ajuste de entrada en Kardex
    wh_res = client.get("/api/v1/branches/warehouses", headers=headers)
    assert wh_res.status_code == 200
    wh_data = wh_res.json()
    warehouses = wh_data.get("items", wh_data) if isinstance(wh_data, dict) else wh_data
    wh_id = warehouses[0]["id"]

    adj_res = client.post("/api/v1/inventory/adjust", json={
        "warehouse_id": wh_id,
        "product_id": prod_id,
        "adjust_type": "in",
        "qty": 20.0,
        "reference": f"AJUSTE-{prod_sku}",
        "notes": "Carga inicial Kardex de prueba"
    }, headers=headers)
    assert adj_res.status_code == 200

    # 3. Consultar Kardex general /api/v1/inventory/moves
    moves_res = client.get(f"/api/v1/inventory/moves?product_id={prod_id}", headers=headers)
    assert moves_res.status_code == 200
    moves_data = moves_res.json()

    assert "items" in moves_data
    assert "summary" in moves_data
    assert moves_data["summary"]["total_moves"] >= 1
    assert moves_data["summary"]["total_inflow"] >= 20.0

    # Verificar datos enriquecidos del item
    first_item = moves_data["items"][0]
    assert "product_name" in first_item
    assert "product_sku" in first_item
    assert "is_inflow" in first_item
    assert "warehouse_name" in first_item

    # 4. Consultar con búsqueda por SKU
    search_res = client.get(f"/api/v1/inventory/moves?search={prod_sku}", headers=headers)
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert len(search_data["items"]) >= 1
    assert search_data["items"][0]["product_sku"] == prod_sku

    # 5. Consultar Kardex individual del producto /api/v1/products/{id}/kardex
    kardex_res = client.get(f"/api/v1/products/{prod_id}/kardex", headers=headers)
    assert kardex_res.status_code == 200
    kardex_data = kardex_res.json()
    assert "kardex_balance" in kardex_data
    assert "current_stock" in kardex_data
    assert "has_discrepancy" in kardex_data
    assert kardex_data["kardex_balance"] == 20.0
    assert kardex_data["has_discrepancy"] is False

    # 6. Probar endpoint de reconciliación /api/v1/products/{id}/sync-kardex
    sync_res = client.post(f"/api/v1/products/{prod_id}/sync-kardex", headers=headers)
    assert sync_res.status_code == 200
    sync_data = sync_res.json()
    assert sync_data["status"] == "success"
    assert sync_data["reconciled_stock"] == 20.0


def test_sales_detail_report_and_excel_export():
    """Verifica que el reporte Detalle de Ventas entregue las 16 columnas requeridas y exporte a Excel y CSV."""
    import openpyxl
    import io

    # 1. Crear producto para la venta
    prod_sku = f"REP-{uuid.uuid4().hex[:6]}"
    prod_barcode = f"740100{uuid.uuid4().hex[:6]}"
    prod_res = client.post("/api/v1/products/", json={
        "name": f"Gaseosa Naranja {uuid.uuid4().hex[:4]}",
        "internal_ref": prod_sku,
        "barcode": prod_barcode,
        "sale_price": 10.0,
        "cost_price": 6.0,
        "stock": 50.0,
        "is_active": True
    }, headers=headers)
    assert prod_res.status_code == 200
    prod_id = prod_res.json()["id"]

    # 2. Registrar venta en efectivo con cliente CF
    sale_payload = {
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "Consumidor Final",
        "sale_type": "pos",
        "lines": [
            {
                "product_id": prod_id,
                "product_name": "Gaseosa Naranja",
                "product_barcode": prod_barcode,
                "qty": 3,
                "unit_price": 10.0,
                "cost_price": 6.0
            }
        ],
        "payments": [
            {
                "payment_method": "cash",
                "amount": 30.00
            }
        ],
        "generate_fel": False
    }
    sale_res = client.post("/api/v1/sales/", json=sale_payload, headers=headers)
    assert sale_res.status_code == 200

    # 3. Consultar endpoint GET /api/v1/reports/sales-detail
    detail_res = client.get(f"/api/v1/reports/sales-detail?search={prod_sku}", headers=headers)
    assert detail_res.status_code == 200
    detail_data = detail_res.json()

    assert "items" in detail_data
    assert "summary" in detail_data
    assert len(detail_data["items"]) >= 1

    row = detail_data["items"][0]
    # Comprobar los 16 campos exactos
    assert "fecha_factura" in row
    assert "sucursal" in row
    assert "categoria" in row
    assert "codigo_de_barras" in row
    assert "articulo" in row
    assert "no_ticket" in row
    assert "cantidad" in row
    assert "pventa" in row
    assert "costo" in row
    assert "total_venta" in row
    assert "excedente" in row
    assert "tipo_de_venta" in row
    assert "f_contado" in row
    assert "f_credito" in row
    assert "id_cliente" in row
    assert "nombre_del_cliente" in row

    # Verificación de cálculos y valores
    assert row["cantidad"] == 3.0
    assert row["pventa"] == 10.00
    assert row["costo"] == 6.0
    assert row["total_venta"] == 30.00
    assert row["excedente"] == 12.00  # 30.00 - 18.00
    assert row["tipo_de_venta"] == "Contado"
    assert row["f_contado"] == 30.00
    assert row["f_credito"] == 0.0
    assert row["nombre_del_cliente"] == "CF"  # Debe normalizar a CF

    # 4. Probar exportación a Excel nativo (.xlsx)
    excel_res = client.get(f"/api/v1/reports/export-sales-detail-excel?search={prod_sku}", headers=headers)
    assert excel_res.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in excel_res.headers["content-type"]
    assert "detalle_de_ventas_" in excel_res.headers["content-disposition"]
    assert len(excel_res.content) > 1000

    # Validar que openpyxl puede leer el archivo Excel y contiene los datos
    wb = openpyxl.load_workbook(io.BytesIO(excel_res.content))
    assert "Detalle de Ventas" in wb.sheetnames
    ws = wb["Detalle de Ventas"]
    # Verificar fila 4 de encabezados (16 columnas)
    header_vals = [ws.cell(row=4, column=c).value for c in range(1, 17)]
    assert "Fecha Factura" in header_vals
    assert "Artículo" in header_vals
    assert "Excedente (Q)" in header_vals
    assert "Nombre del Cliente" in header_vals

    # 5. Probar exportación a CSV
    csv_res = client.get(f"/api/v1/reports/export-sales-detail-csv?search={prod_sku}", headers=headers)
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    csv_text = csv_res.content.decode("utf-8")
    assert "Fecha factura,Sucursal,categoria,codigo de barras,articulo" in csv_text
    assert "Gaseosa Naranja" in csv_text


def test_cashier_closure_exact_change_and_tendered_amount():
    """
    Test para validar que el cuadre de caja y conteo de efectivo cuadren exactamente
    cuando un cliente paga en efectivo con un billete de mayor denominación (vuelto/cambio).
    Ejemplo: Venta de Q20.44 pagada con billete de Q50.00 (Cambio Q29.56).
    El efectivo neto ingresado a gaveta debe ser estrictamente Q20.44, y el esperado Fondo (100) + Venta (20.44) = Q120.44.
    """
    # 1. Obtener sucursal y bodega
    b_res = client.get("/api/v1/branches/", headers=headers)
    assert b_res.status_code == 200
    branch_id = b_res.json()[0]["id"]

    wh_res = client.get("/api/v1/warehouses/", headers=headers)
    wh_id = wh_res.json()[0]["id"] if wh_res.status_code == 200 and wh_res.json() else branch_id

    # Cerrar cualquier sesión previa que haya quedado abierta en la base de pruebas
    prev_act = client.get("/api/v1/cashier/sessions/active", headers=headers)
    if prev_act.status_code == 200 and prev_act.json().get("has_active_session"):
        p_id = prev_act.json()["session"]["id"]
        client.post(f"/api/v1/cashier/sessions/{p_id}/close", json={"closing_amount_real": 0.0}, headers=headers)

    # 2. Abrir sesión con fondo de Q 100.00
    open_res = client.post("/api/v1/cashier/open", json={
        "branch_id": branch_id,
        "opening_amount": 100.0,
        "notes": "Turno de prueba cuadre con vuelto"
    }, headers=headers)
    assert open_res.status_code == 200
    sess_id = open_res.json()["id"]

    # 3. Crear producto de prueba con precio de Q 20.44
    p_sku = f"CASH-{uuid.uuid4().hex[:6]}"
    prod_res = client.post("/api/v1/products/", json={
        "name": "Aceite Patrona Prueba Cuadre",
        "sku": p_sku,
        "barcode": f"BAR-{p_sku}",
        "sale_price": 18.25,
        "cost_price": 12.00,
        "tax_rate": 12.00  # 18.25 + 2.19 = 20.44 total
    }, headers=headers)
    assert prod_res.status_code == 200
    prod_id = prod_res.json()["id"]

    # 4. Registrar venta en efectivo donde el cliente paga con billete de Q 50.00
    sale_res = client.post("/api/v1/sales/", json={
        "branch_id": branch_id,
        "warehouse_id": wh_id,
        "cashier_session_id": sess_id,
        "customer_nit": "CF",
        "customer_name": "CONSUMIDOR FINAL",
        "lines": [{
            "product_id": prod_id,
            "product_name": "Aceite Patrona Prueba Cuadre",
            "qty": 1.0,
            "unit_price": 18.25,
            "tax_rate": 12.00,
            "includes_tax": False
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 50.0  # El cliente entregó billete de 50
        }]
    }, headers=headers)
    assert sale_res.status_code == 200
    sale_data = sale_res.json()
    assert round(sale_data["total"], 2) == 20.44

    # 5. Consultar sesión activa de caja
    active_res = client.get(f"/api/v1/cashier/sessions/active?branch_id={branch_id}", headers=headers)
    assert active_res.status_code == 200
    session_info = active_res.json()["session"]

    # Validaciones matemáticas exactas
    assert session_info["total_sales"] == 20.44
    assert session_info["total_cash_sales"] == 20.44  # NO debe ser 50.00
    assert session_info["opening_balance"] == 100.00
    # Efectivo esperado = 100 + 20.44 = 120.44 (NO 150.00)
    assert session_info["expected_cash"] == 120.44

    # 6. Realizar el Cierre de Caja contando Q 120.44
    close_res = client.post(f"/api/v1/cashier/sessions/{sess_id}/close", json={
        "closing_amount_real": 120.44,
        "notes": "Cierre exacto"
    }, headers=headers)
    assert close_res.status_code == 200
    close_data = close_res.json()
    assert close_data["closing_balance_expected"] == 120.44
    assert close_data["closing_balance_real"] == 120.44
    assert close_data["difference"] == 0.00  # Cuadra al centavo!



