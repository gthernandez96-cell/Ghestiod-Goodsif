"""Pruebas para sincronización offline IndexedDB y catálogo rápido."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token
import uuid

client = TestClient(app)

COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

token = create_access_token(data={"sub": str(USER_ID), "email": "test_admin@empresa.com", "company_id": str(COMPANY_ID)})
headers = {"Authorization": f"Bearer {token}"}


def test_pos_catalog_endpoint():
    """Validar endpoint de descarga rápida de catálogo para IndexedDB."""
    res = client.get("/api/v1/products/pos-catalog", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "products" in data
    assert "categories" in data
    assert "total_products" in data
    assert "server_time" in data
    assert isinstance(data["products"], list)
    assert isinstance(data["categories"], list)


def test_offline_sale_idempotency():
    """Validar creación de venta offline con client_offline_id y evitar duplicados en reintentos."""
    client_offline_id = "off_test_" + str(uuid.uuid4())

    sale_payload = {
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "CONSUMIDOR FINAL",
        "sale_type": "pos",
        "generate_fel": True,
        "client_offline_id": client_offline_id,
        "lines": [{
            "product_id": str(uuid.uuid4()),
            "product_name": "Producto Offline Test",
            "qty": 1,
            "unit_price": 50.0,
            "cost_price": 30.0
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 56.0
        }],
        "notes": "Venta realizada en modo offline"
    }

    # 1. Primer envío
    res1 = client.post("/api/v1/sales/", json=sale_payload, headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert "id" in data1
    assert "number" in data1
    first_id = data1["id"]
    first_number = data1["number"]

    # 2. Reintento de sincronización con el mismo client_offline_id (debe ser idempotente)
    res2 = client.post("/api/v1/sales/", json=sale_payload, headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["id"] == first_id
    assert data2["number"] == first_number
    assert data2.get("idempotent") is True


def test_product_warehouses_stock_endpoint():
    """Validar endpoint de existencias multi-bodega."""
    # 1. Crear o listar productos
    res_list = client.get("/api/v1/products/?limit=1", headers=headers)
    assert res_list.status_code == 200
    items = res_list.json().get("items", [])
    if items:
        p_id = items[0]["id"]
        res = client.get(f"/api/v1/products/{p_id}/warehouses-stock", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "product_id" in data
        assert "product_name" in data
        assert "total_stock" in data
        assert "warehouses" in data
        assert isinstance(data["warehouses"], list)


def test_product_live_stock_levels():
    """Validar endpoint de stock en tiempo real para actualización automática del POS."""
    res = client.get("/api/v1/products/stock-levels", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "stock" in data
    assert isinstance(data["stock"], dict)
    assert "count" in data

    # Probar alias /stock-sync
    res_sync = client.get("/api/v1/products/stock-sync", headers=headers)
    assert res_sync.status_code == 200
    assert "stock" in res_sync.json()


def test_pos_and_inventory_stock_consistency_and_ghost_warehouse_isolation():
    """Garantizar que existencias en inventario, detalle y POS sean 100% idénticas y aislar bodegas fantasma."""
    import uuid
    import asyncio
    from decimal import Decimal
    from app.core.database import AsyncSessionLocal
    from app.models.inventory import StockQuant

    # 1. Crear producto y establecer stock inicial vía /adjust-stock
    p_name = f"Test Consistency Product {uuid.uuid4().hex[:6]}"
    res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 25.0,
    }, headers=headers)
    assert res.status_code in (200, 201)
    prod_id = res.json()["id"]

    adj_res = client.post(f"/api/v1/products/{prod_id}/adjust-stock", json={
        "new_qty": 50.0,
        "reason": "initial",
        "notes": "Stock inicial prueba"
    }, headers=headers)
    assert adj_res.status_code in (200, 201)

    # 2. Inyectar un quant en una bodega fantasma/inexistente (simulando residuo antiguo o huérfano)
    ghost_wh_id = uuid.uuid4()
    async def _inject_ghost():
        async with AsyncSessionLocal() as session:
            g_quant = StockQuant(
                product_id=uuid.UUID(prod_id),
                warehouse_id=ghost_wh_id,
                qty_on_hand=Decimal("-10.0"),
                qty_reserved=Decimal("0.0"),
                avg_cost=Decimal("15.0")
            )
            session.add(g_quant)
            await session.commit()
    asyncio.run(_inject_ghost())

    # 3. Consultar stock desde catálogo general (/products/?is_active=true)
    cat_res = client.get(f"/api/v1/products/?search={p_name}", headers=headers)
    assert cat_res.status_code == 200
    cat_items = cat_res.json().get("items", [])
    assert len(cat_items) == 1
    # Debe ignorar la bodega fantasma y reportar exactamente 50.0 (no 40.0)
    assert cat_items[0]["stock"] == 50.0

    # 4. Consultar stock desde detalle de producto (/products/{id})
    det_res = client.get(f"/api/v1/products/{prod_id}", headers=headers)
    assert det_res.status_code == 200
    assert det_res.json()["total_stock"] == 50.0

    # 5. Consultar stock desde endpoint de sincronización POS (/products/stock-levels)
    pos_res = client.get("/api/v1/products/stock-levels", headers=headers)
    assert pos_res.status_code == 200
    assert pos_res.json()["stock"].get(prod_id) == 50.0

    # 6. Consultar stock desde catálogo POS (/products/pos-catalog)
    pos_cat = client.get("/api/v1/products/pos-catalog", headers=headers)
    assert pos_cat.status_code == 200
    matched = [p for p in pos_cat.json().get("products", []) if p["id"] == prod_id]
    assert len(matched) == 1
    assert matched[0]["stock"] == 50.0

