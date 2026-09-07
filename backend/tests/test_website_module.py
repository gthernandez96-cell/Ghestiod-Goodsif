"""Pruebas automatizadas para la Página Web / Landing y el Gestor Administrativo."""
import pytest
from fastapi.testclient import TestClient
import io
import uuid
from app.main import app
from app.core.security import create_access_token

client = TestClient(app)

COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

token = create_access_token(data={"sub": str(USER_ID), "email": "test_admin@empresa.com", "company_id": str(COMPANY_ID)})
headers = {"Authorization": f"Bearer {token}"}



def test_public_landing_page_html():
    """Verificar que la página /login y / cargan la web corporativa con los giros de negocio."""
    res = client.get("/login")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    html = res.text
    
    # Verificar giros solicitados por el usuario
    assert "Ferreterías" in html
    assert "Abarroterías" in html
    assert "Panaderías" in html
    assert "Restaurantes" in html
    assert "Facturación Electrónica SAT" in html
    assert "WhatsApp" in html
    assert "Iniciar Sesión" in html


def test_website_config_public_api():
    """Verificar endpoint público GET /api/v1/website/config."""
    res = client.get("/api/v1/website/config")
    assert res.status_code == 200
    data = res.json()
    assert "site_title" in data
    assert "hero_title" in data
    assert "sectors" in data
    assert len(data["sectors"]) >= 4
    
    # Verificar que existen los sectores clave
    sector_names = [s["name"] for s in data["sectors"]]
    assert any("Ferretería" in n for n in sector_names)
    assert any("Abarrotería" in n for n in sector_names)
    assert any("Panadería" in n for n in sector_names)
    assert any("Restaurante" in n for n in sector_names)


def test_website_config_update_and_reset():
    """Verificar actualización y reseteo de la configuración del sitio."""
    # 1. Modificar configuración
    new_title = "El Mejor POS para Guatemala 2026"
    new_wa = "50299998888"
    update_res = client.put(
        "/api/v1/website/config",
        headers=headers,
        json={
            "hero_title": new_title,
            "whatsapp_number": new_wa,
        }
    )
    assert update_res.status_code == 200
    res_data = update_res.json()
    assert res_data["success"] is True
    assert res_data["data"]["hero_title"] == new_title
    assert res_data["data"]["whatsapp_number"] == new_wa

    # 2. Verificar que el cambio se refleja en el endpoint público
    check_res = client.get("/api/v1/website/config")
    assert check_res.status_code == 200
    assert check_res.json()["hero_title"] == new_title

    # 3. Restablecer valores de fábrica
    reset_res = client.post("/api/v1/website/reset-defaults", headers=headers)
    assert reset_res.status_code == 200
    assert reset_res.json()["success"] is True


def test_website_image_upload():
    """Verificar subida de imagen para banners o sectores."""
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    files = {"file": ("banner_test.png", io.BytesIO(fake_png), "image/png")}

    res = client.post("/api/v1/website/upload-image", headers=headers, files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "/static/uploads/website/" in data["url"]


def test_website_admin_html_page():
    """Verificar que la ruta /website-admin carga el template administrativo."""
    res = client.get("/website-admin", headers=headers)
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    html = res.text
    assert "Administrador de la Página Web" in html
    assert "Ingresar a Módulos ERP" in html
    assert "Giros de Negocio" in html

