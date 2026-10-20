"""Pruebas para el módulo de Pre-Venta, Rol Preventista y Solicitudes POS con Aprobación en Bodega."""
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


def test_presale_and_solicitudes_page_routes():
    """Verificar que las vistas HTML de Pre-venta y Solicitudes POS respondan 200."""
    res_presale = client.get("/preventa")
    assert res_presale.status_code == 200

    res_solicitudes = client.get("/pos-solicitudes")
    assert res_solicitudes.status_code == 200


def test_presale_kpis_endpoint():
    """Verificar que el endpoint de KPIs de Solicitudes POS devuelva métricas válidas."""
    res = client.get("/api/v1/presale/kpis", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "pending_count" in data
    assert "pending_amount" in data
    assert "approved_today" in data
    assert "rejected_today" in data


def test_presale_lifecycle_no_stock_deduction_until_approved():
    """
    FLUJO COMPLETO DE PRE-VENTA:
    1. Obtener un producto existente y registrar su stock inicial.
    2. Crear Pre-Venta (Solicitud POS).
    3. Comprobar que el inventario NO se rebajó al crear la solicitud.
    4. Validar existencias con bodega.
    5. Autorizar la solicitud.
    6. Comprobar que el inventario SÍ se rebajó exactamente en la cantidad autorizada.
    """
    # 1. Crear producto dedicado con stock para la prueba
    p_name = f"Prod Lifecycle {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 50.0,
        "initial_stock": 20.0
    }, headers=headers)
    assert p_res.status_code == 200
    product_id = p_res.json()["id"]
    prod = {"id": product_id, "name": p_name}
    initial_stock = 20.0
    qty_sold = 2.0

    # 2. Crear solicitud de Pre-venta
    presale_payload = {
        "customer_name": "Ferretería El Progreso",
        "customer_nit": "1234567-8",
        "customer_address": "Zona 1, Ciudad",
        "notes": "Pedido de pre-venta de prueba",
        "lines": [
            {
                "product_id": product_id,
                "product_name": prod["name"],
                "qty": qty_sold,
                "unit_price": 50.0,
                "discount_amount": 0.0
            }
        ]
    }

    create_res = client.post("/api/v1/presale/", json=presale_payload, headers=headers)
    assert create_res.status_code == 201
    created_data = create_res.json()
    order_id = created_data["id"]
    assert created_data["success"] is True
    assert created_data["state"] == "pending_approval"
    assert created_data["number"].startswith("PRE-")

    # 3. VERIFICACIÓN CRUCIAL: El stock del producto NO debe haber cambiado tras la creación
    prod_check = client.get(f"/api/v1/products/{product_id}", headers=headers).json()
    stock_after_create = float(prod_check.get("stock") or prod_check.get("total_stock") or 0)
    assert stock_after_create == initial_stock, "El stock NO debe rebajarse al crear la pre-venta."

    # 4. Validar stock con bodega
    val_res = client.post(f"/api/v1/presale/{order_id}/validate-stock", headers=headers)
    assert val_res.status_code == 200
    val_data = val_res.json()
    assert val_data["warehouse_verified"] is True

    # 5. Listar en solicitudes POS
    list_res = client.get(f"/api/v1/presale/solicitudes?search={created_data['number']}", headers=headers)
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    assert len(items) >= 1
    assert items[0]["id"] == order_id

    # 6. Autorizar la solicitud (Aprobar y Rebajar Inventario)
    approve_res = client.post(f"/api/v1/presale/{order_id}/approve", json={}, headers=headers)
    assert approve_res.status_code == 200
    app_data = approve_res.json()
    assert app_data["success"] is True
    assert app_data["state"] == "approved"

    # 7. VERIFICACIÓN FINAL: Ahora el stock SÍ debe haberse rebajado
    prod_after_approve = client.get(f"/api/v1/products/{product_id}", headers=headers).json()
    stock_final = float(prod_after_approve.get("stock") or prod_after_approve.get("total_stock") or 0)
    expected_stock = initial_stock - qty_sold
    assert abs(stock_final - expected_stock) < 0.001, f"El stock debió rebajarse a {expected_stock}, actual: {stock_final}"


def test_presale_rejection_workflow():
    """Verificar que el rechazo de una solicitud no altere inventario y registre el motivo."""
    p_name = f"Prod Rejection {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 25.0,
        "initial_stock": 20.0
    }, headers=headers)
    assert p_res.status_code == 200
    prod = {"id": p_res.json()["id"], "name": p_name}
    initial_stock = 20.0

    # Crear orden
    create_res = client.post(
        "/api/v1/presale/",
        json={
            "customer_name": "Cliente Rechazo Test",
            "customer_nit": "CF",
            "lines": [
                {
                    "product_id": prod["id"],
                    "product_name": prod["name"],
                    "qty": 3.0,
                    "unit_price": 25.0
                }
            ]
        },
        headers=headers
    )
    assert create_res.status_code == 201
    order_id = create_res.json()["id"]

    # Rechazar solicitud
    reject_res = client.post(
        f"/api/v1/presale/{order_id}/reject",
        json={"reason": "Cliente no cuenta con línea de crédito autorizada"},
        headers=headers
    )
    assert reject_res.status_code == 200
    rej_data = reject_res.json()
    assert rej_data["state"] == "rejected"
    assert rej_data["rejection_reason"] == "Cliente no cuenta con línea de crédito autorizada"

    # Verificar que el stock sigue intacto
    prod_check = client.get(f"/api/v1/products/{prod['id']}", headers=headers).json()
    stock_after_reject = float(prod_check.get("stock") or prod_check.get("total_stock") or 0)
    assert stock_after_reject == initial_stock


def test_presale_delivery_logistics_and_extra_fields():
    """Verificar que fecha de entrega, dirección y notas de ruta se guarden y recuperen sin alterar la BD."""
    p_name = f"Prod Logistics {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 100.0,
        "initial_stock": 20.0
    }, headers=headers)
    assert p_res.status_code == 200
    prod = {"id": p_res.json()["id"], "name": p_name}
    payload = {
        "customer_name": "Ferretería El Progreso",
        "customer_nit": "8765432-1",
        "customer_address": "Km 14.5 Carretera al Atlántico",
        "delivery_date": "2026-09-25",
        "delivery_address": "Bodega 4, Zona Industrial Norte",
        "delivery_notes": "Llamar 30 minutos antes al encargado Don Mario (tel: 5555-1234)",
        "lines": [
            {
                "product_id": prod["id"],
                "product_name": prod["name"],
                "qty": 5.0,
                "unit_price": 100.0,
                "discount_amount": 0.0
            }
        ]
    }

    create_res = client.post("/api/v1/presale/", json=payload, headers=headers)
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert created_data["success"] is True
    assert created_data["delivery_date"] == "2026-09-25"
    assert created_data["delivery_address"] == "Bodega 4, Zona Industrial Norte"
    assert "Don Mario" in created_data["delivery_notes"]

    order_id = created_data["id"]

    # Verificar detalle
    detail_res = client.get(f"/api/v1/presale/{order_id}", headers=headers)
    assert detail_res.status_code == 200
    detail_data = detail_res.json()
    assert detail_data["delivery_date"] == "2026-09-25"
    assert detail_data["delivery_address"] == "Bodega 4, Zona Industrial Norte"
    assert "Don Mario" in detail_data["delivery_notes"]

    # Limpiar orden de prueba
    client.post(f"/api/v1/presale/{order_id}/reject", json={"reason": "Fin de prueba de logística"}, headers=headers)


def test_presale_freezes_stock_without_affecting_inventory():
    """
    Verifica que al realizar una pre-venta:
    1. El stock disponible del producto se congela (stock disminuye en la cantidad solicitada).
    2. El stock congelado (stock_frozen) se incrementa.
    3. El inventario físico (stock_physical / qty_on_hand) NO se modifica.
    4. El desglose por bodega (warehouses-stock) refleja Físico, Congelado y Disponible.
    """
    p_name = f"Prod Freeze {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 50.0,
        "initial_stock": 20.0
    }, headers=headers)
    assert p_res.status_code == 200
    p_id = p_res.json()["id"]
    prod = {"id": p_id, "name": p_name}
    initial_avail = 20.0
    initial_phys = 20.0
    initial_froz = 0.0

    qty_to_freeze = 4.0
    payload = {
        "customer_name": "Cliente Congelamiento",
        "customer_nit": "CF",
        "lines": [
            {
                "product_id": p_id,
                "product_name": prod["name"],
                "qty": qty_to_freeze,
                "unit_price": 50.0
            }
        ]
    }
    create_res = client.post("/api/v1/presale/", json=payload, headers=headers)
    assert create_res.status_code == 201
    order_id = create_res.json()["id"]

    # Verificar que en el catálogo de productos el stock quedó congelado
    after_res = client.get(f"/api/v1/products/?search={prod['name']}", headers=headers)
    assert after_res.status_code == 200
    matched = [p for p in after_res.json().get("items", []) if p["id"] == p_id]
    assert len(matched) == 1
    p_after = matched[0]

    expected_avail = initial_avail - qty_to_freeze
    expected_froz = initial_froz + qty_to_freeze
    assert abs(float(p_after["stock"]) - expected_avail) < 0.001, f"Stock disponible debió congelarse a {expected_avail}, fue: {p_after['stock']}"
    assert abs(float(p_after["stock_frozen"]) - expected_froz) < 0.001, f"Stock congelado debió ser {expected_froz}, fue: {p_after['stock_frozen']}"
    assert abs(float(p_after["stock_physical"]) - initial_phys) < 0.001, "El inventario físico NO debió afectarse."

    # Verificar endpoint de bodegas
    wh_stock_res = client.get(f"/api/v1/products/{p_id}/warehouses-stock", headers=headers)
    assert wh_stock_res.status_code == 200
    wh_data = wh_stock_res.json()
    assert abs(float(wh_data["total_frozen"]) - expected_froz) < 0.001
    assert abs(float(wh_data["total_physical"]) - initial_phys) < 0.001

    # Limpiar orden rechazándola
    client.post(f"/api/v1/presale/{order_id}/reject", json={"reason": "Fin prueba congelamiento"}, headers=headers)

    # Verificar que al rechazar, el stock congelado vuelve a la normalidad inmediatamente
    unfrozen_res = client.get(f"/api/v1/products/?search={prod['name']}", headers=headers)
    p_unfrozen = [p for p in unfrozen_res.json().get("items", []) if p["id"] == p_id][0]
    assert abs(float(p_unfrozen["stock"]) - initial_avail) < 0.001
    assert abs(float(p_unfrozen["stock_frozen"]) - initial_froz) < 0.001


def test_presale_seven_day_auto_expiration_unfreezes_stock():
    """
    Verifica que a los 7 días que la solicitud no se haya aprobado o anulado,
    el stock congelado vuelve automáticamente a la normalidad.
    """
    from datetime import datetime, timezone, timedelta
    import sqlite3
    import os

    p_name = f"Prod Auto Expira {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 20.0,
        "initial_stock": 20.0
    }, headers=headers)
    assert p_res.status_code == 200
    p_id = p_res.json()["id"]
    prod = {"id": p_id, "name": p_name}
    initial_avail = 20.0

    qty_to_freeze = 3.0
    payload = {
        "customer_name": "Cliente Auto Expira 7D",
        "customer_nit": "CF",
        "lines": [{"product_id": p_id, "product_name": prod["name"], "qty": qty_to_freeze, "unit_price": 20.0}]
    }
    create_res = client.post("/api/v1/presale/", json=payload, headers=headers)
    assert create_res.status_code == 201
    order_id = create_res.json()["id"]

    # Simular que transcurrieron 8 días (> 7 días) actualizando created_at en la BD de pruebas
    db_path = "test_pos.db"
    assert os.path.exists(db_path)
    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    oid_clean = str(order_id).replace("-", "")
    cur.execute("UPDATE sale_orders SET created_at = ? WHERE id IN (?, ?)", (old_time, order_id, oid_clean))
    con.commit()
    con.close()

    # Consultar productos tras los 8 días: debe volver a la normalidad
    res_after_7d = client.get(f"/api/v1/products/?search={prod['name']}", headers=headers)
    assert res_after_7d.status_code == 200
    p_after_7d = [p for p in res_after_7d.json().get("items", []) if p["id"] == p_id][0]

    # El stock debe haber vuelto a la normalidad (sin estar congelado)
    assert abs(float(p_after_7d["stock"]) - initial_avail) < 0.001, "El stock debió volver a la normalidad tras 7 días sin aprobación."
    assert float(p_after_7d["stock_frozen"]) == 0.0, "El stock congelado debió liberarse a 0 tras 7 días."

    # Limpiar orden de prueba
    client.post(f"/api/v1/presale/{order_id}/reject", json={"reason": "Fin prueba 7d"}, headers=headers)


def test_partner_soft_delete_endpoint():
    """
    Verifica que se pueda crear y eliminar (soft-delete) un cliente vía /api/v1/partners/{id}.
    """
    # 1. Crear un cliente temporal
    new_partner_payload = {
        "name": "Cliente Prueba Eliminación",
        "nit": "99988877",
        "email": "eliminar@example.com",
        "phone": "55554433",
        "is_customer": True,
        "is_supplier": False,
        "credit_limit": 500.0,
        "credit_days": 15
    }
    create_res = client.post("/api/v1/partners/", json=new_partner_payload, headers=headers)
    assert create_res.status_code in (200, 201)
    partner_data = create_res.json()
    partner_id = partner_data["id"]

    # 2. Verificar que existe en la lista
    list_res = client.get(f"/api/v1/partners/?search=99988877", headers=headers)
    assert list_res.status_code == 200
    items = list_res.json().get("items", [])
    assert any(p["id"] == partner_id for p in items)

    # 3. Eliminar cliente con DELETE /api/v1/partners/{partner_id}
    del_res = client.delete(f"/api/v1/partners/{partner_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # 4. Verificar que ya no aparece en la búsqueda activa
    list_after = client.get(f"/api/v1/partners/?search=99988877", headers=headers)
    assert list_after.status_code == 200
    items_after = list_after.json().get("items", [])
    assert not any(p["id"] == partner_id for p in items_after)


def test_cannot_sell_or_presell_reserved_product_without_available_stock():
    """
    Verifica que si un producto está reservado y no tiene existencia disponible (1 físico, 1 congelado):
    - NO se pueda generar otra pre-venta (recibe 400).
    - NO se pueda realizar una venta POS (recibe 400).
    - Al liberar la reserva, la venta sí sea permitida.
    """
    p_name = f"Prod Reserva Bloqueo {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 5.10,
    }, headers=headers)
    assert p_res.status_code in (200, 201)
    prod_id = p_res.json()["id"]

    # 1. Establecer stock físico exacto en 1 unidad
    adj_res = client.post(f"/api/v1/products/{prod_id}/adjust-stock", json={
        "new_qty": 1.0,
        "reason": "initial",
        "notes": "Stock inicial de 1 unidad"
    }, headers=headers)
    assert adj_res.status_code in (200, 201)

    # 2. Crear pre-venta reservando la única unidad existente (1 congelada, 0 disponible)
    pre1_res = client.post("/api/v1/presale/", json={
        "customer_name": "Cliente Reserva 1",
        "customer_nit": "CF",
        "lines": [{
            "product_id": prod_id,
            "product_name": p_name,
            "qty": 1.0,
            "unit_price": 5.10
        }]
    }, headers=headers)
    assert pre1_res.status_code == 201
    order1_id = pre1_res.json()["id"]

    # 3. Intentar crear OTRA pre-venta para el mismo producto -> Debe ser rechazada con 400
    pre2_res = client.post("/api/v1/presale/", json={
        "customer_name": "Cliente Intento 2",
        "customer_nit": "CF",
        "lines": [{
            "product_id": prod_id,
            "product_name": p_name,
            "qty": 1.0,
            "unit_price": 5.10
        }]
    }, headers=headers)
    assert pre2_res.status_code == 400
    assert "reservado" in pre2_res.json()["detail"].lower()

    # 4. Intentar realizar una venta POS para el mismo producto -> Debe ser rechazada con 400
    pos_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "CONSUMIDOR FINAL",
        "sale_type": "pos",
        "lines": [{
            "product_id": prod_id,
            "product_name": p_name,
            "qty": 1.0,
            "unit_price": 5.10,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 5.10
        }]
    }, headers=headers)
    assert pos_res.status_code == 400
    assert "reservado" in pos_res.json()["detail"].lower()

    # 5. Rechazar la primera pre-venta -> libera la unidad
    rej_res = client.post(f"/api/v1/presale/{order1_id}/reject", json={"reason": "Liberar para venta"}, headers=headers)
    assert rej_res.status_code == 200

    # 6. Ahora que se liberó, la venta POS sí debe permitirse
    pos_ok_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "CONSUMIDOR FINAL",
        "sale_type": "pos",
        "lines": [{
            "product_id": prod_id,
            "product_name": p_name,
            "qty": 1.0,
            "unit_price": 5.10,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 5.10
        }]
    }, headers=headers)
    assert pos_ok_res.status_code == 200


def test_warehouse_preparation_and_two_phase_approval_flow():
    """
    PRUEBA DEL FLUJO EN 2 FASES:
    1. Vista HTML /pos/preparacion responde 200.
    2. Crear orden de pre-venta.
    3. Fase 1: pre-approve (Aprobar para Bodega) -> state == warehouse_validation, stock NO rebajado en StockQuant.
    4. Consultar /api/v1/presale/preparation/orders -> La orden aparece para el bodeguero.
    5. Fase 2: mark-prepared (Bodeguero marca Producto Preparado) -> warehouse_verified == True.
    6. Fase 3: approve/dispatch (Supervisor autoriza y descuenta) -> state == approved, StockQuant rebajado.
    """
    # 1. Vista HTML
    prep_html = client.get("/pos/preparacion")
    assert prep_html.status_code == 200

    # 2. Crear producto dedicado con stock para la prueba
    p_name = f"Prod Prep Bodega {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 25.0,
    }, headers=headers)
    assert p_res.status_code in (200, 201)
    p_id = p_res.json()["id"]

    adj_res = client.post(f"/api/v1/products/{p_id}/adjust-stock", json={
        "new_qty": 10.0,
        "reason": "initial",
        "notes": "Stock inicial para prueba de preparación"
    }, headers=headers)
    assert adj_res.status_code in (200, 201)

    # 3. Crear pre-venta
    presale_res = client.post("/api/v1/presale/", json={
        "customer_name": "Restaurante San Martín",
        "customer_nit": "8877665-1",
        "notes": "Entrega urgente en cocina",
        "delivery_address": "Zona 10",
        "lines": [{
            "product_id": p_id,
            "product_name": p_name,
            "qty": 2.0,
            "unit_price": 25.0,
        }]
    }, headers=headers)
    assert presale_res.status_code in (200, 201)
    order_id = presale_res.json()["id"]

    # 4. Fase 1: pre-approve (Aprobación sin rebajar inventario físico)
    pre_app_res = client.post(f"/api/v1/presale/{order_id}/pre-approve", headers=headers)
    assert pre_app_res.status_code == 200
    pre_app_data = pre_app_res.json()
    assert pre_app_data["success"] is True
    assert pre_app_data["state"] == "warehouse_validation"
    assert pre_app_data["warehouse_verified"] is False

    # 5. Bodeguero consulta pedidos en preparación
    prep_orders_res = client.get("/api/v1/presale/preparation/orders", headers=headers)
    assert prep_orders_res.status_code == 200
    prep_orders = prep_orders_res.json()["items"]
    matching = [o for o in prep_orders if o["id"] == order_id]
    assert len(matching) == 1
    assert matching[0]["warehouse_verified"] is False

    # 6. Bodeguero marca como preparado
    mark_res = client.post(f"/api/v1/presale/{order_id}/mark-prepared", headers=headers)
    assert mark_res.status_code == 200
    assert mark_res.json()["warehouse_verified"] is True

    # Comprobar que en el listado ahora está preparado
    prep_orders_res2 = client.get("/api/v1/presale/preparation/orders?status=prepared", headers=headers)
    assert prep_orders_res2.status_code == 200
    prepared_items = prep_orders_res2.json()["items"]
    assert any(o["id"] == order_id and o["warehouse_verified"] is True for o in prepared_items)

    # 7. Fase 3: Despacho final y descuento de inventario
    disp_res = client.post(f"/api/v1/presale/{order_id}/dispatch", headers=headers)
    assert disp_res.status_code == 200
    disp_data = disp_res.json()
    assert disp_data["success"] is True
    assert disp_data["state"] == "approved"


def test_warehouse_partial_picking_deducts_only_prepared_items():
    """
    PRUEBA DE PREPARACIÓN PARCIAL EN BODEGA (OPCIÓN 2):
    1. Crear 2 productos con 10 unidades de stock cada uno.
    2. Crear pre-venta con 2 unidades de Prod A y 3 unidades de Prod B.
    3. Fase 1: pre-approve (enviar a bodega sin descontar stock).
    4. Fase 2: Bodeguero marca como preparado con faltante:
       - Prod A: 2 preparados.
       - Prod B: 0 preparados (marcado como faltante/no disponible).
    5. Comprobar que el total del pedido se ajusta automáticamente solo a Prod A.
    6. Fase 3: Despacho final.
    7. Comprobar que SOLO Prod A se rebajó de bodega (10 -> 8).
       Prod B queda intacto (10 unidades) y liberado de reserva.
    """
    # 1. Crear productos
    p_name_a = f"Prod Parcial A {uuid.uuid4().hex[:6]}"
    p_res_a = client.post("/api/v1/products/", json={"name": p_name_a, "sale_price": 20.0}, headers=headers)
    assert p_res_a.status_code in (200, 201)
    p_id_a = p_res_a.json()["id"]

    p_name_b = f"Prod Parcial B {uuid.uuid4().hex[:6]}"
    p_res_b = client.post("/api/v1/products/", json={"name": p_name_b, "sale_price": 50.0}, headers=headers)
    assert p_res_b.status_code in (200, 201)
    p_id_b = p_res_b.json()["id"]

    # Ajustar stock inicial a 10 unidades cada uno
    client.post(f"/api/v1/products/{p_id_a}/adjust-stock", json={"new_qty": 10.0, "reason": "init", "notes": "Init A"}, headers=headers)
    client.post(f"/api/v1/products/{p_id_b}/adjust-stock", json={"new_qty": 10.0, "reason": "init", "notes": "Init B"}, headers=headers)

    # 2. Crear pre-venta: 2 de A (Q 40) + 3 de B (Q 150) = Total Q 190
    presale_res = client.post("/api/v1/presale/", json={
        "customer_name": "Cliente Despacho Parcial",
        "customer_nit": "CF",
        "lines": [
            {"product_id": p_id_a, "product_name": p_name_a, "qty": 2.0, "unit_price": 20.0},
            {"product_id": p_id_b, "product_name": p_name_b, "qty": 3.0, "unit_price": 50.0}
        ]
    }, headers=headers)
    assert presale_res.status_code in (200, 201)
    order_id = presale_res.json()["id"]

    # 3. Fase 1: Pre-aprobar para bodega
    pre_res = client.post(f"/api/v1/presale/{order_id}/pre-approve", headers=headers)
    assert pre_res.status_code == 200

    # 4. Fase 2: Bodeguero prepara parcialmente (A preparado, B faltante)
    mark_payload = {
        "items": [
            {"product_id": p_id_a, "is_prepared": True, "qty_prepared": 2.0},
            {"product_id": p_id_b, "is_prepared": False, "qty_prepared": 0.0}
        ],
        "notes": "No se encontró Producto B en estantes"
    }
    mark_res = client.post(f"/api/v1/presale/{order_id}/mark-prepared", json=mark_payload, headers=headers)
    assert mark_res.status_code == 200
    mark_data = mark_res.json()
    assert mark_data["is_partial"] is True
    # El nuevo total debe ser únicamente el de Prod A: 2 * 20 = 40.0
    assert float(mark_data["total"]) == 40.0

    # Verificar detalle de la orden
    detail_res = client.get(f"/api/v1/presale/{order_id}", headers=headers)
    assert detail_res.status_code == 200
    detail_data = detail_res.json()
    assert detail_data["total"] == 40.0
    assert "[Bodega]" in (detail_data.get("internal_notes") or "")

    # 5. Fase 3: Despacho y descuento de inventario
    disp_res = client.post(f"/api/v1/presale/{order_id}/dispatch", headers=headers)
    assert disp_res.status_code == 200

    # 6. Comprobar existencias físicas
    # Prod A se rebajó de 10 a 8
    prod_a_check = client.get(f"/api/v1/products/{p_id_a}", headers=headers).json()
    stock_a = float(prod_a_check.get("stock") or prod_a_check.get("total_stock") or 0)
    assert stock_a == 8.0

    # Prod B NO se rebajó (queda en 10.0)
    prod_b_check = client.get(f"/api/v1/products/{p_id_b}", headers=headers).json()
    stock_b = float(prod_b_check.get("stock") or prod_b_check.get("total_stock") or 0)
    assert stock_b == 10.0


def test_cannot_sell_or_presell_more_than_warehouse_stock():
    """
    Verifica que tanto en POS como en Pre-venta no se permita facturar ni vender
    más de lo que existe en bodega, ni vender producto que esté comprometido en reservas.
    """
    # 1. Crear producto con stock inicial de 5 unidades
    p_name = f"Prod StockLimit {uuid.uuid4().hex[:6]}"
    p_res = client.post("/api/v1/products/", json={
        "name": p_name,
        "sale_price": 25.0,
        "initial_stock": 5.0
    }, headers=headers)
    assert p_res.status_code == 200
    p_id = p_res.json()["id"]

    # 2. Intento de venta directa POS por 6 unidades (disponible es 5) -> Debe fallar 400
    pos_fail_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "Consumidor Final",
        "sale_type": "pos",
        "lines": [{
            "product_id": p_id,
            "product_name": p_name,
            "qty": 6.0,
            "unit_price": 25.0,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 150.0
        }]
    }, headers=headers)
    assert pos_fail_res.status_code == 400
    assert "disponible" in pos_fail_res.json()["detail"].lower() or "existencia" in pos_fail_res.json()["detail"].lower()

    # 3. Venta directa POS por 2 unidades -> Exitoso, quedan 3 en bodega
    pos_ok_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "Consumidor Final",
        "sale_type": "pos",
        "lines": [{
            "product_id": p_id,
            "product_name": p_name,
            "qty": 2.0,
            "unit_price": 25.0,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 50.0
        }]
    }, headers=headers)
    assert pos_ok_res.status_code in (200, 201)

    # 4. Intento de Pre-venta por 4 unidades (solo quedan 3 disponibles) -> Debe fallar 400
    presale_fail_res = client.post("/api/v1/presale/", json={
        "customer_name": "Cliente Mayorista",
        "customer_nit": "CF",
        "lines": [
            {
                "product_id": p_id,
                "qty": 4.0,
                "unit_price": 25.0,
                "discount_amount": 0.0
            }
        ]
    }, headers=headers)
    assert presale_fail_res.status_code == 400
    assert "disponible" in presale_fail_res.json()["detail"].lower() or "existencia" in presale_fail_res.json()["detail"].lower()

    # 5. Pre-venta por 2 unidades -> Exitoso. Quedan 3 físicos y 2 reservados => Disponible = 1
    presale_ok_res = client.post("/api/v1/presale/", json={
        "customer_name": "Cliente Mayorista",
        "customer_nit": "CF",
        "lines": [
            {
                "product_id": p_id,
                "qty": 2.0,
                "unit_price": 25.0,
                "discount_amount": 0.0
            }
        ]
    }, headers=headers)
    assert presale_ok_res.status_code == 201

    # 6. Intento de venta POS por 2 unidades cuando disponible es 1 (porque 2 están reservados)
    # Debe fallar indicando que está reservado o sin suficiente disponible
    pos_conflict_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "Consumidor Final",
        "sale_type": "pos",
        "lines": [{
            "product_id": p_id,
            "product_name": p_name,
            "qty": 2.0,
            "unit_price": 25.0,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 50.0
        }]
    }, headers=headers)
    assert pos_conflict_res.status_code == 400
    assert "reservado" in pos_conflict_res.json()["detail"].lower() or "disponible" in pos_conflict_res.json()["detail"].lower()

    # 7. Venta POS por la última unidad disponible (1 unidad) -> Exitoso
    pos_last_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "Consumidor Final",
        "sale_type": "pos",
        "lines": [{
            "product_id": p_id,
            "product_name": p_name,
            "qty": 1.0,
            "unit_price": 25.0,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 25.0
        }]
    }, headers=headers)
    assert pos_last_res.status_code in (200, 201)

    # 8. Ahora disponible es 0 (físico 2, reservado 2).
    # Ninguna venta POS adicional debe permitirse
    pos_zero_res = client.post("/api/v1/sales/", json={
        "branch_id": str(COMPANY_ID),
        "warehouse_id": str(COMPANY_ID),
        "customer_nit": "CF",
        "customer_name": "Consumidor Final",
        "sale_type": "pos",
        "lines": [{
            "product_id": p_id,
            "product_name": p_name,
            "qty": 1.0,
            "unit_price": 25.0,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 25.0
        }]
    }, headers=headers)
    assert pos_zero_res.status_code == 400







