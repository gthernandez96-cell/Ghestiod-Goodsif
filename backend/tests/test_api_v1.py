"""Pruebas de integración para la API v1 de POS Empresarial."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "app" in data
    assert "status" in data


def test_api_info():
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    data = response.json()
    assert "environment" in data


def test_page_routes():
    pages = ["/login", "/dashboard", "/pos", "/inventory", "/purchases", "/transfers", "/customers", "/reports", "/settings"]
    for page in pages:
        res = client.get(page)
        assert res.status_code == 200


def test_auth_features():
    # 1. Listado público de sucursales
    b_res = client.get("/api/v1/auth/public-branches")
    assert b_res.status_code == 200
    assert isinstance(b_res.json(), list)

    # 2. Login rápido con PIN
    pin_res = client.post("/api/v1/auth/pin-login", json={"pin": "1234"})
    assert pin_res.status_code == 200
    assert "access_token" in pin_res.json()

    # 3. Solicitud de recuperación de contraseña
    forgot_res = client.post("/api/v1/auth/forgot-password", json={"email": "admin@empresa.com"})
    assert forgot_res.status_code == 200
    assert forgot_res.json()["status"] == "success"

