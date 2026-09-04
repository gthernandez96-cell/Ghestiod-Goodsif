"""Pruebas para el módulo de Gestión de Usuarios y Roles Predeterminados."""
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


def test_users_html_page():
    """Verificar que la página HTML de Usuarios y Roles responde con status 200."""
    res = client.get("/users", headers=headers)
    assert res.status_code == 200
    assert "Usuarios" in res.text


def test_list_predefined_roles():
    """Verificar que el endpoint retorna los 5 roles predeterminados del sistema."""
    res = client.get("/api/v1/roles", headers=headers)
    assert res.status_code == 200
    roles = res.json()
    assert len(roles) >= 5
    codes = [r["code"] for r in roles]
    assert "admin" in codes
    assert "store_manager" in codes
    assert "cashier" in codes
    assert "inventory_manager" in codes
    assert "accountant" in codes


def test_roles_matrix():
    """Verificar endpoint de matriz de roles y permisos."""
    res = client.get("/api/v1/roles/matrix", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "roles" in data
    assert "modules" in data
    assert len(data["modules"]) >= 5


def test_users_kpis():
    """Verificar métricas y resumen ejecutivo de usuarios."""
    res = client.get("/api/v1/users/kpis", headers=headers)
    assert res.status_code == 200
    kpis = res.json()
    assert "total_users" in kpis
    assert "active_users" in kpis
    assert "cashiers_count" in kpis
    assert "admins_count" in kpis


def test_user_crud_and_roles_lifecycle():
    """Probar creación de usuario con rol, actualización, toggle de estado y reset de contraseña."""
    unique_suffix = uuid.uuid4().hex[:6]
    username = f"cajero_{unique_suffix}"
    email = f"{username}@pos.gt"

    # 1. Crear nuevo usuario Cajero
    create_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Cajero de Turno {unique_suffix}",
            "username": username,
            "email": email,
            "password": "Password123!",
            "role_code": "cashier",
            "supervisor_pin": "1234",
            "phone": "+502 5555-1234"
        },
        headers=headers
    )
    assert create_res.status_code == 201
    user_data = create_res.json()
    user_id = user_data["id"]

    # 2. Listar usuarios con filtro de búsqueda
    list_res = client.get(f"/api/v1/users?search={username}", headers=headers)
    assert list_res.status_code == 200
    users_list = list_res.json()
    assert len(users_list) == 1
    u = users_list[0]
    assert u["role_code"] == "cashier"
    assert u["has_supervisor_pin"] is True
    assert u["is_active"] is True

    # 3. Actualizar usuario: Cambiar rol a Gerente de Sucursal y PIN
    update_res = client.put(
        f"/api/v1/users/{user_id}",
        json={
            "full_name": f"Gerente {unique_suffix}",
            "role_code": "store_manager",
            "supervisor_pin": "9999",
            "phone": "+502 4444-9999"
        },
        headers=headers
    )
    assert update_res.status_code == 200

    # Verificar que el cambio se guardó
    chk_res = client.get(f"/api/v1/users?search={username}", headers=headers)
    chk_user = chk_res.json()[0]
    assert chk_user["role_code"] == "store_manager"
    assert chk_user["full_name"] == f"Gerente {unique_suffix}"

    # 4. Alternar estado (suspender acceso)
    toggle_res = client.post(f"/api/v1/users/{user_id}/toggle-status", headers=headers)
    assert toggle_res.status_code == 200
    assert toggle_res.json()["is_active"] is False

    # 5. Restablecer contraseña
    pwd_res = client.post(
        f"/api/v1/users/{user_id}/reset-password",
        json={"new_password": "NewSecretPassword2026!"},
        headers=headers
    )
    assert pwd_res.status_code == 200
    assert "Contraseña restablecida" in pwd_res.json()["message"]


def test_cashier_cannot_create_or_modify_products_and_prices():
    """Verificar que un usuario con rol Cajero recibe 403 al intentar agregar o modificar productos."""
    # 1. Crear un usuario exclusivo con rol Cajero
    cajero_suffix = uuid.uuid4().hex[:6]
    cajero_username = f"cajero_restriccion_{cajero_suffix}"
    cajero_email = f"{cajero_username}@pos.gt"

    create_user_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Cajero Restringido {cajero_suffix}",
            "username": cajero_username,
            "email": cajero_email,
            "password": "Password123!",
            "role_code": "cashier"
        },
        headers=headers
    )
    assert create_user_res.status_code == 201
    cajero_id = create_user_res.json()["id"]

    # 2. Generar token de autenticación para este cajero
    cajero_token = create_access_token(data={"sub": str(cajero_id), "email": cajero_email, "company_id": str(COMPANY_ID)})
    cajero_headers = {"Authorization": f"Bearer {cajero_token}"}

    # 3. Intentar crear un producto como Cajero -> Debe retornar 403 Forbidden
    prod_create_res = client.post(
        "/api/v1/products/",
        json={
            "name": "Producto No Permitido",
            "sale_price": 50.00,
            "cost_price": 30.00
        },
        headers=cajero_headers
    )
    assert prod_create_res.status_code == 403
    assert "El usuario con rol Cajero no tiene autorización" in prod_create_res.json()["detail"]

    # 4. Crear un producto como Admin
    admin_prod_res = client.post(
        "/api/v1/products/",
        json={
            "name": f"Producto Valido {cajero_suffix}",
            "sale_price": 100.00,
            "cost_price": 60.00
        },
        headers=headers
    )
    assert admin_prod_res.status_code == 200
    prod_id = admin_prod_res.json()["id"]

    # 5. Intentar modificar el producto / alterar precio como Cajero -> Debe retornar 403 Forbidden
    prod_update_res = client.put(
        f"/api/v1/products/{prod_id}",
        json={
            "sale_price": 15.00  # Intento de alterar precio
        },
        headers=cajero_headers
    )
    assert prod_update_res.status_code == 403
    assert "El usuario con rol Cajero no tiene autorización" in prod_update_res.json()["detail"]


def test_cashier_cannot_create_or_modify_customers():
    """Verificar que un usuario con rol Cajero recibe 403 al intentar agregar o modificar clientes."""
    cajero_suffix = uuid.uuid4().hex[:6]
    cajero_username = f"cajero_partner_{cajero_suffix}"
    cajero_email = f"{cajero_username}@pos.gt"

    create_user_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Cajero Clientes {cajero_suffix}",
            "username": cajero_username,
            "email": cajero_email,
            "password": "Password123!",
            "role_code": "cashier"
        },
        headers=headers
    )
    assert create_user_res.status_code == 201
    cajero_id = create_user_res.json()["id"]

    cajero_token = create_access_token(data={"sub": str(cajero_id), "email": cajero_email, "company_id": str(COMPANY_ID)})
    cajero_headers = {"Authorization": f"Bearer {cajero_token}"}

    # 1. Intentar crear un cliente como Cajero -> 403 Forbidden
    cust_create_res = client.post(
        "/api/v1/partners/",
        json={
            "name": "Cliente No Permitido Por Cajero",
            "partner_type": "customer",
            "nit": "CF"
        },
        headers=cajero_headers
    )
    assert cust_create_res.status_code == 403
    assert "El usuario con rol Cajero no tiene autorización" in cust_create_res.json()["detail"]

    # 2. Crear cliente como Admin
    admin_cust_res = client.post(
        "/api/v1/partners/",
        json={
            "name": f"Cliente Valido {cajero_suffix}",
            "partner_type": "customer",
            "nit": f"NIT-{cajero_suffix}"
        },
        headers=headers
    )
    assert admin_cust_res.status_code == 200
    cust_id = admin_cust_res.json()["id"]

    # 3. Intentar modificar cliente como Cajero -> 403 Forbidden
    cust_update_res = client.put(
        f"/api/v1/partners/{cust_id}",
        json={
            "name": "Nombre Modificado Ilegalmente",
            "credit_limit": 100000.0
        },
        headers=cajero_headers
    )
    assert cust_update_res.status_code == 403
    assert "El usuario con rol Cajero no tiene autorización" in cust_update_res.json()["detail"]


def test_page_auth_guard_security():
    """Verificar que las páginas privadas redirigen a /login si no hay sesión activa."""
    # 1. Sin autenticación: la raíz '/' redirige a '/login'
    res_root = client.get("/", follow_redirects=False)
    assert res_root.status_code == 302
    assert res_root.headers["location"] == "/login"

    # 2. Sin autenticación: '/dashboard' redirige a '/login'
    res_dash = client.get("/dashboard", follow_redirects=False)
    assert res_dash.status_code == 302
    assert "/login" in res_dash.headers["location"]

    # 3. Sin autenticación: '/pos' redirige a '/login'
    res_pos = client.get("/pos", follow_redirects=False)
    assert res_pos.status_code == 302
    assert "/login" in res_pos.headers["location"]

    # 4. Sin autenticación: '/login' muestra el formulario (200 OK)
    res_login = client.get("/login", follow_redirects=False)
    assert res_login.status_code == 200
    assert "Ingresar al Sistema" in res_login.text or "INGRESAR AL SISTEMA" in res_login.text or "POS Empresarial" in res_login.text

    # 5. Con token en cookies: '/dashboard' responde 200 OK
    res_auth_dash = client.get("/dashboard", cookies={"access_token": token}, follow_redirects=False)
    assert res_auth_dash.status_code == 200



