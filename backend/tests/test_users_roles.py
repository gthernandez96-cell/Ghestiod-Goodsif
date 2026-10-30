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
    # 1. Sin autenticación: la raíz '/' responde 200 OK mostrando el login (compatible con Health Check de DigitalOcean)
    res_root = client.get("/", follow_redirects=False)
    assert res_root.status_code == 200

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
    res_auth_dash = client.get("/dashboard", headers={"Cookie": f"access_token={token}"}, follow_redirects=False)
    assert res_auth_dash.status_code == 200


def test_cashier_cannot_register_credit_payments():
    """Verificar que un usuario con rol Cajero recibe 403 Forbidden al intentar registrar abonos de crédito."""
    cajero_suffix = uuid.uuid4().hex[:6]
    cajero_username = f"cajero_abono_{cajero_suffix}"
    cajero_email = f"{cajero_username}@pos.gt"

    # 1. Crear usuario exclusivo con rol Cajero
    create_user_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Cajero Abonos {cajero_suffix}",
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

    # 2. Crear un cliente con saldo de crédito como Admin
    admin_cust_res = client.post(
        "/api/v1/partners/",
        json={
            "name": f"Cliente Con Crédito {cajero_suffix}",
            "partner_type": "customer",
            "nit": f"NIT-CR-{cajero_suffix}",
            "credit_limit": 5000.0
        },
        headers=headers
    )
    assert admin_cust_res.status_code == 200
    cust_id = admin_cust_res.json()["id"]

    # 3. Intentar registrar un abono como Cajero en partners -> Debe retornar 403 Forbidden
    pay_res_partners = client.post(
        f"/api/v1/partners/{cust_id}/credit-payments",
        json={
            "amount": 100.0,
            "payment_method": "cash",
            "reference": "Abono Cajero Prohibido"
        },
        headers=cajero_headers
    )
    assert pay_res_partners.status_code == 403
    assert "El usuario con rol Cajero no tiene autorización para registrar abonos de crédito" in pay_res_partners.json()["detail"]

    # 4. Intentar registrar un abono como Cajero en AR -> Debe retornar 403 Forbidden
    pay_res_ar = client.post(
        f"/api/v1/ar/invoices/{cust_id}/payments",
        json={
            "amount": 100.0,
            "payment_method": "cash",
            "reference": "Abono Cajero Prohibido AR"
        },
        headers=cajero_headers
    )
    assert pay_res_ar.status_code == 403
    assert "El usuario con rol Cajero no tiene autorización para registrar abonos de crédito" in pay_res_ar.json()["detail"]

    # 5. Como Admin sí debe ser permitido
    pay_res_admin = client.post(
        f"/api/v1/partners/{cust_id}/credit-payments",
        json={
            "amount": 50.0,
            "payment_method": "cash",
            "reference": "Abono Legal Admin"
        },
        headers=headers
    )
    assert pay_res_admin.status_code == 200
    assert pay_res_admin.json()["amount_paid"] == 50.0


def test_cashier_only_sees_assigned_terminal_caja():
    """Verificar que un usuario con rol Cajero únicamente puede ver la caja/sucursal asignada."""
    # 1. Crear 2 sucursales de prueba si no existen
    b_suffix = uuid.uuid4().hex[:6]
    b1_res = client.post(
        "/api/v1/branches/",
        json={"name": f"Sucursal Alfa {b_suffix}", "code": f"ALF_{b_suffix}"},
        headers=headers
    )
    assert b1_res.status_code == 200
    b1_id = b1_res.json()["id"]

    b2_res = client.post(
        "/api/v1/branches/",
        json={"name": f"Sucursal Beta {b_suffix}", "code": f"BET_{b_suffix}"},
        headers=headers
    )
    assert b2_res.status_code == 200
    b2_id = b2_res.json()["id"]

    # 2. El Administrador ve todas las terminales
    admin_term_res = client.get("/api/v1/cashier/terminals", headers=headers)
    assert admin_term_res.status_code == 200
    admin_terms = admin_term_res.json()
    assert len(admin_terms) >= 2
    branch_ids_admin = [t["branch_id"] for t in admin_terms]
    assert b1_id in branch_ids_admin
    assert b2_id in branch_ids_admin

    # 3. Crear un usuario Cajero asignado EXCLUSIVAMENTE a Sucursal Beta
    cajero_username = f"cajero_beta_{b_suffix}"
    cajero_email = f"{cajero_username}@pos.gt"
    c_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Cajero Sucursal Beta {b_suffix}",
            "username": cajero_username,
            "email": cajero_email,
            "password": "Password123!",
            "role_code": "cashier",
            "current_branch_id": b2_id
        },
        headers=headers
    )
    assert c_res.status_code == 201
    cajero_id = c_res.json()["id"]

    cajero_token = create_access_token(data={"sub": str(cajero_id), "email": cajero_email, "company_id": str(COMPANY_ID)})
    cajero_headers = {"Authorization": f"Bearer {cajero_token}"}

    # 4. Consultar terminales como Cajero -> Debe ver ÚNICAMENTE la caja de su sucursal asignada (Beta)
    cashier_term_res = client.get("/api/v1/cashier/terminals", headers=cajero_headers)
    assert cashier_term_res.status_code == 200
    cashier_terms = cashier_term_res.json()
    assert len(cashier_terms) == 1
    assert cashier_terms[0]["branch_id"] == b2_id
    assert f"Sucursal Beta {b_suffix}" in cashier_terms[0]["terminal_name"]


def test_presale_seller_user_and_permissions():
    """Verificar creación y permisos del rol Pre-Vendedor (presale_seller)."""
    p_suffix = uuid.uuid4().hex[:6]
    p_username = f"prevendedor_{p_suffix}"
    p_email = f"{p_username}@pos.gt"

    # 1. Crear usuario Pre-Vendedor
    create_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Pre-Vendedor {p_suffix}",
            "username": p_username,
            "email": p_email,
            "password": "Password123!",
            "role_code": "presale_seller"
        },
        headers=headers
    )
    assert create_res.status_code == 201
    user_id = create_res.json()["id"]

    # 2. Verificar en lista de usuarios
    users_res = client.get("/api/v1/users", headers=headers)
    assert users_res.status_code == 200
    user_data = next((u for u in users_res.json() if u["username"] == p_username), None)
    assert user_data is not None
    assert user_data["role_code"] == "presale_seller"
    assert user_data["role_name"] == "Pre-Vendedor"
    assert user_data["role_badge_color"] == "#d97706"

    # 3. Verificar KPIs
    kpis_res = client.get("/api/v1/users/kpis", headers=headers)
    assert kpis_res.status_code == 200
    assert "presale_count" in kpis_res.json()
    assert kpis_res.json()["presale_count"] >= 1

    # 4. Verificar lista de roles predeterminados
    roles_res = client.get("/api/v1/roles", headers=headers)
    assert roles_res.status_code == 200
    presale_role = next((r for r in roles_res.json() if r["code"] == "presale_seller"), None)
    assert presale_role is not None
    assert presale_role["name"] == "Pre-Vendedor"
    modules = [p["module"] for p in presale_role["permissions"]]
    assert "Pre-Venta" in modules
    assert "Cotizaciones" in modules
    assert "Créditos & AR" in modules

    # 5. Token para Pre-Vendedor y verificación de acceso a clientes
    p_token = create_access_token(data={"sub": str(user_id), "email": p_email, "company_id": str(COMPANY_ID)})
    p_headers = {"Authorization": f"Bearer {p_token}"}

    # Pre-Vendedor puede crear clientes para sus órdenes y cotizaciones
    cust_res = client.post(
        "/api/v1/partners/",
        json={
            "name": f"Cliente Preventa {p_suffix}",
            "partner_type": "customer",
            "nit": f"NIT-PRE-{p_suffix}"
        },
        headers=p_headers
    )
    assert cust_res.status_code == 200

    # 6. Condición estricta: Pre-Vendedor NO puede consultar existencias de inventario por bodega -> 403 Forbidden
    prod_res = client.post(
        "/api/v1/products/",
        json={
            "name": f"Producto Test {p_suffix}",
            "sale_price": 50.0,
            "cost_price": 30.0,
            "barcode": f"BAR-{p_suffix}"
        },
        headers=headers
    )
    assert prod_res.status_code in (200, 201)
    prod_id = prod_res.json()["id"]

    stock_res = client.get(f"/api/v1/products/{prod_id}/warehouses-stock", headers=p_headers)
    assert stock_res.status_code == 200
    assert "warehouses" in stock_res.json()
    assert "total_available" in stock_res.json()

    # 7. Pre-Vendedor en /api/v1/products/ tiene existencias de las bodegas de la sucursal disponibles
    prod_list_pre = client.get("/api/v1/products/", headers=p_headers)
    assert prod_list_pre.status_code == 200
    pre_items = prod_list_pre.json()["items"]
    test_p_pre = next((i for i in pre_items if i["id"] == prod_id), None)
    assert test_p_pre is not None
    assert test_p_pre["stock"] is not None
    assert test_p_pre["stock_physical"] is not None

    # Pre-Vendedor en /stock-levels retorna existencias reales de la sucursal
    levels_pre = client.get("/api/v1/products/stock-levels", headers=p_headers)
    assert levels_pre.status_code == 200
    assert "stock" in levels_pre.json()

    # 8. Para el RESTO de usuarios (ej. Admin), las existencias SÍ están disponibles (números reales)
    admin_stock_wh = client.get(f"/api/v1/products/{prod_id}/warehouses-stock", headers=headers)
    assert admin_stock_wh.status_code == 200

    admin_prod_list = client.get("/api/v1/products/", headers=headers)
    assert admin_prod_list.status_code == 200
    admin_items = admin_prod_list.json()["items"]
    test_p_admin = next((i for i in admin_items if i["id"] == prod_id), None)
    assert test_p_admin is not None
    assert test_p_admin["stock"] is not None
    assert test_p_admin["stock_physical"] is not None

    # 9. Pre-Vendedor SÍ puede consultar el detalle del producto para ver la Ficha Técnica (PDF, notas, descripción)
    detail_pre = client.get(f"/api/v1/products/{prod_id}", headers=p_headers)
    assert detail_pre.status_code == 200
    pre_detail_json = detail_pre.json()
    assert pre_detail_json["name"] == f"Producto Test {p_suffix}"
    assert "pdf_datasheet_url" in pre_detail_json
    assert "description" in pre_detail_json
    assert pre_detail_json["total_stock"] is not None
    assert isinstance(pre_detail_json["warehouses"], list)


def test_admin_stock_visibility_management():
    """Test para la gestión administrativa de visibilidad de stock por usuario en POS, Pre-Venta y Cotizaciones."""
    s = uuid.uuid4().hex[:6]
    c_username = f"cajero_stock_{s}"
    c_email = f"cajero_stock_{s}@empresa.com"

    # 1. Crear usuario cajero de prueba
    create_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Cajero Test Stock {s}",
            "username": c_username,
            "email": c_email,
            "password": "Password123!",
            "role_code": "cashier"
        },
        headers=headers
    )
    assert create_res.status_code == 201
    c_id = create_res.json()["id"]

    c_token = create_access_token(data={"sub": str(c_id), "email": c_email, "company_id": str(COMPANY_ID)})
    c_headers = {"Authorization": f"Bearer {c_token}"}

    # 2. Verificar que un usuario no-admin NO puede consultar ni modificar la visibilidad de stock (403 Forbidden)
    vis_fail = client.get("/api/v1/users/stock-visibility", headers=c_headers)
    assert vis_fail.status_code == 403
    assert "restringido" in vis_fail.json()["detail"].lower() or "administrador" in vis_fail.json()["detail"].lower()

    put_fail = client.put(
        "/api/v1/users/stock-visibility",
        json={"users": [{"user_id": c_id, "can_view_stock": True, "stock_pos": True, "stock_presale": True, "stock_quotes": True}]},
        headers=c_headers
    )
    assert put_fail.status_code == 403

    # 3. El Administrador SÍ puede consultar la visibilidad (200 OK)
    admin_vis = client.get("/api/v1/users/stock-visibility", headers=headers)
    assert admin_vis.status_code == 200
    user_item = next((u for u in admin_vis.json() if u["id"] == c_id), None)
    assert user_item is not None
    assert "can_view_stock" in user_item
    assert "stock_pos" in user_item
    assert "stock_presale" in user_item
    assert "stock_quotes" in user_item

    # 4. Crear producto de prueba con inventario
    p_res = client.post(
        "/api/v1/products/",
        json={
            "name": f"Producto Visibilidad Stock {s}",
            "sale_price": 75.0,
            "stock": 40.0
        },
        headers=headers
    )
    assert p_res.status_code in (200, 201)
    p_id = p_res.json()["id"]

    # 5. El Administrador desactiva la visibilidad de existencias para este cajero
    update_res = client.put(
        "/api/v1/users/stock-visibility",
        json={
            "users": [
                {
                    "user_id": c_id,
                    "can_view_stock": False,
                    "stock_pos": False,
                    "stock_presale": False,
                    "stock_quotes": False
                }
            ]
        },
        headers=headers
    )
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "ok"

    # 6. Al consultar con token del cajero restringido: existencias vienen ocultas (None / vacías)
    cat_res = client.get("/api/v1/products/pos-catalog", headers=c_headers)
    assert cat_res.status_code == 200
    cat_items = cat_res.json()["products"]
    p_in_cat = next((x for x in cat_items if x["id"] == p_id), None)
    assert p_in_cat is not None
    assert p_in_cat["stock"] is None
    assert p_in_cat["stock_available"] is None

    levels_res = client.get("/api/v1/products/stock-levels", headers=c_headers)
    assert levels_res.status_code == 200
    assert levels_res.json() == {}

    detail_res = client.get(f"/api/v1/products/{p_id}", headers=c_headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["total_stock"] is None
    assert detail_res.json()["warehouses"] == []

    wh_stock_res = client.get(f"/api/v1/products/{p_id}/warehouses-stock", headers=c_headers)
    assert wh_stock_res.status_code == 200
    assert wh_stock_res.json() == []

    # 7. El Administrador reactiva la visibilidad de existencias para el usuario
    reactivate_res = client.patch(
        f"/api/v1/users/{c_id}/stock-visibility",
        json={
            "user_id": c_id,
            "can_view_stock": True,
            "stock_pos": True,
            "stock_presale": True,
            "stock_quotes": True
        },
        headers=headers
    )
    assert reactivate_res.status_code == 200
    assert reactivate_res.json()["can_view_stock"] is True

    # 8. Ahora el usuario vuelve a ver las existencias correctamente
    cat_res_after = client.get("/api/v1/products/pos-catalog", headers=c_headers)
    assert cat_res_after.status_code == 200
    p_in_cat_after = next((x for x in cat_res_after.json()["products"] if x["id"] == p_id), None)
    assert p_in_cat_after is not None
    assert p_in_cat_after["stock"] is not None


def test_inventory_manager_role_and_permissions():
    """Verificar rol Encargado de Inventario / Bodeguero: creación, permisos de módulos y conteo KPI."""
    # 1. Verificar definición en roles
    roles_res = client.get("/api/v1/roles", headers=headers)
    assert roles_res.status_code == 200
    roles = roles_res.json()
    inv_role = next((r for r in roles if r["code"] == "inventory_manager"), None)
    assert inv_role is not None
    assert "Encargado de Inventario" in inv_role["name"]
    assert "Bodeguero" in inv_role["name"]
    assert "Preparación en Bodega" in inv_role["description"]
    assert "Traslados" in inv_role["description"]
    assert "Fabricación" in inv_role["description"]

    # 2. Crear usuario con rol Encargado de Inventario / Bodeguero
    suffix = uuid.uuid4().hex[:6]
    username = f"bodeguero_{suffix}"
    email = f"{username}@pos.gt"

    create_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Bodeguero Principal {suffix}",
            "username": username,
            "email": email,
            "password": "Password123!",
            "role_code": "inventory_manager",
            "phone": "+502 5555-7890"
        },
        headers=headers
    )
    assert create_res.status_code == 201
    user_id = create_res.json()["id"]

    # 3. Verificar listado y búsqueda
    list_res = client.get(f"/api/v1/users?search={username}", headers=headers)
    assert list_res.status_code == 200
    data = list_res.json()
    assert len(data) == 1
    assert data[0]["role_code"] == "inventory_manager"
    assert "Bodeguero" in data[0]["role_name"]

    # 4. Verificar filtro por rol inventory_manager
    filter_res = client.get("/api/v1/users?role=inventory_manager", headers=headers)
    assert filter_res.status_code == 200
    matching = [u for u in filter_res.json() if u["username"] == username]
    assert len(matching) == 1

    # 5. Verificar KPIs de usuarios
    kpis_res = client.get("/api/v1/users/kpis", headers=headers)
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert kpis.get("inventory_count", 0) >= 1


def test_accountant_role_and_permissions():
    """Verificar el rol de Contador / Finanzas: catálogo, creación, filtros, y conteo de KPIs."""
    # 1. Verificar catálogo de roles
    roles_res = client.get("/api/v1/roles", headers=headers)
    assert roles_res.status_code == 200
    roles = roles_res.json()
    acct_role = next((r for r in roles if r["code"] in ("accountant", "contador")), None)
    assert acct_role is not None
    assert "Contador" in acct_role["name"]
    assert "cobrar" in acct_role["description"].lower() or "cuentas" in acct_role["description"].lower()
    perms = acct_role.get("permissions", [])
    assert any("cobrar" in p["module"].lower() for p in perms)
    assert any("pagar" in p["module"].lower() for p in perms)
    assert any("reportes" in p["module"].lower() for p in perms)

    # 2. Crear usuario con rol Contador / Finanzas
    suffix = uuid.uuid4().hex[:6]
    username = f"contador_{suffix}"
    email = f"{username}@pos.gt"

    create_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Contador Principal {suffix}",
            "username": username,
            "email": email,
            "password": "Password123!",
            "role_code": "accountant",
            "phone": "+502 2222-3333"
        },
        headers=headers
    )
    assert create_res.status_code == 201
    user_id = create_res.json()["id"]

    # 3. Verificar listado y búsqueda
    list_res = client.get(f"/api/v1/users?search={username}", headers=headers)
    assert list_res.status_code == 200
    data = list_res.json()
    assert len(data) == 1
    assert data[0]["role_code"] in ("accountant", "contador")
    assert "Contador" in data[0]["role_name"]

    # 4. Verificar filtro por rol accountant
    filter_res = client.get("/api/v1/users?role=accountant", headers=headers)
    assert filter_res.status_code == 200
    matching = [u for u in filter_res.json() if u["username"] == username]
    assert len(matching) == 1

    # 5. Verificar KPIs de usuarios (accountants_count)
    kpis_res = client.get("/api/v1/users/kpis", headers=headers)
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert kpis.get("accountants_count", 0) >= 1

    # 6. Actualizar usuario conservando rol
    update_res = client.put(
        f"/api/v1/users/{user_id}",
        json={
            "full_name": f"Lic. Contador {suffix}",
            "role_code": "accountant",
            "phone": "+502 2222-9999"
        },
        headers=headers
    )
    assert update_res.status_code == 200

    # 7. Verificar que los cambios se reflejaron
    chk_res = client.get(f"/api/v1/users?search={username}", headers=headers)
    assert chk_res.status_code == 200
    chk_data = chk_res.json()[0]
    assert chk_data["full_name"] == f"Lic. Contador {suffix}"
    assert chk_data["role_code"] in ("accountant", "contador")


def test_store_manager_role_and_permissions():
    """Verificar el rol de Gerente de Sucursal / Supervisor: catálogo, permisos, bloqueo de creación de empresas y KPIs."""
    # 1. Verificar catálogo de roles
    roles_res = client.get("/api/v1/roles", headers=headers)
    assert roles_res.status_code == 200
    roles = roles_res.json()
    mgr_role = next((r for r in roles if r["code"] == "store_manager"), None)
    assert mgr_role is not None
    assert "Gerente de Sucursal" in mgr_role["name"]
    assert "Supervisor" in mgr_role["name"]
    assert "Punto de Venta" in mgr_role["description"]
    assert "Inventarios" in mgr_role["description"]
    assert "Bodegas" in mgr_role["description"]

    # 2. Crear usuario con rol Gerente de Sucursal / Supervisor
    suffix = uuid.uuid4().hex[:6]
    username = f"gerente_{suffix}"
    email = f"{username}@pos.gt"

    create_res = client.post(
        "/api/v1/users",
        json={
            "full_name": f"Gerente Sucursal {suffix}",
            "username": username,
            "email": email,
            "password": "Password123!",
            "role_code": "store_manager",
            "phone": "+502 5555-1234",
            "supervisor_pin": "1234"
        },
        headers=headers
    )
    assert create_res.status_code == 201
    mgr_id = create_res.json()["id"]

    # 3. Verificar listado y búsqueda
    list_res = client.get(f"/api/v1/users?search={username}", headers=headers)
    assert list_res.status_code == 200
    data = list_res.json()
    assert len(data) == 1
    assert data[0]["role_code"] == "store_manager"
    assert "Gerente" in data[0]["role_name"]

    # 4. Verificar filtro por rol store_manager
    filter_res = client.get("/api/v1/users?role=store_manager", headers=headers)
    assert filter_res.status_code == 200
    matching = [u for u in filter_res.json() if u["username"] == username]
    assert len(matching) == 1

    # 5. Verificar KPIs de usuarios (managers_count)
    kpis_res = client.get("/api/v1/users/kpis", headers=headers)
    assert kpis_res.status_code == 200
    kpis = kpis_res.json()
    assert kpis.get("managers_count", 0) >= 1

    # 6. Autenticarse como Gerente de Sucursal
    mgr_token = create_access_token(data={"sub": str(mgr_id), "email": email, "company_id": str(COMPANY_ID)})
    mgr_headers = {"Authorization": f"Bearer {mgr_token}"}

    # 7. Intentar crear una nueva empresa -> 403 Forbidden (Bloqueado para Gerente)
    company_create_res = client.post(
        "/api/v1/companies",
        json={
            "name": f"Empresa No Autorizada {suffix}",
            "nit": "CF"
        },
        headers=mgr_headers
    )
    assert company_create_res.status_code == 403
    assert "no tiene autorización para crear nuevas empresas" in company_create_res.json()["detail"]

    # 8. Consultar empresa actual -> 200 OK (Permitido para Gerente)
    curr_res = client.get("/api/v1/companies/current", headers=mgr_headers)
    assert curr_res.status_code == 200













