"""Pruebas para el módulo de Previsión de Demanda y Reorden Inteligente."""
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


def test_forecast_demand_endpoint():
    """Validar endpoint de cálculo de demanda y cobertura de stock."""
    res = client.get("/api/v1/forecast/demand?days=30&target_cover_days=15", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert "items" in data
    assert "analysis_period_days" in data
    assert data["analysis_period_days"] == 30
    assert "critical_count" in data["summary"]
    assert "total_suggested_cost" in data["summary"]
    assert isinstance(data["items"], list)

    if len(data["items"]) > 0:
        first = data["items"][0]
        assert "product_id" in first
        assert "product_name" in first
        assert "current_stock" in first
        assert "daily_velocity" in first
        assert "days_of_stock" in first
        assert "risk_level" in first
        assert "suggested_reorder_qty" in first


def test_forecast_reorder_suggestions_endpoint():
    """Validar sugerencias de reorden agrupadas por proveedor."""
    res = client.get("/api/v1/forecast/reorder-suggestions?days=15", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert "supplier_groups" in data
    assert isinstance(data["supplier_groups"], list)


def test_forecast_ai_summary_endpoint():
    """Validar reporte ejecutivo con diagnóstico en lenguaje natural."""
    res = client.get("/api/v1/forecast/ai-summary?days=30", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "headline" in data
    assert "insights" in data
    assert isinstance(data["insights"], list)
    assert len(data["insights"]) > 0


def test_forecast_generate_po_endpoint():
    """Validar generación automática de orden de compra en borrador."""
    # 1. Obtener productos
    prods_res = client.get("/api/v1/products/?is_active=true", headers=headers)
    assert prods_res.status_code == 200
    prods = prods_res.json()
    items = prods.get("items", []) if isinstance(prods, dict) else prods

    if not items:
        pytest.skip("No hay productos disponibles para probar generación de orden")

    prod = items[0]
    payload = {
        "supplier_id": "unassigned",
        "notes": "Orden de prueba automatizada desde test_forecast",
        "items": [
            {
                "product_id": str(prod["id"]),
                "product_name": prod["name"],
                "qty": 10.0,
                "unit_price": float(prod.get("cost_price") or 15.0)
            }
        ]
    }

    res = client.post("/api/v1/forecast/generate-po", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "created"
    assert "po_id" in data
    assert "po_number" in data
    assert data["total_lines"] == 1
    assert data["total_amount"] > 0
