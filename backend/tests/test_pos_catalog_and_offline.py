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
