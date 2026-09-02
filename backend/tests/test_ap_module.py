"""Pruebas para el módulo de Cuentas por Pagar (AP) y su integración con Compras."""
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


def test_ap_page_route():
    """Verificar que la vista HTML de Cuentas por Pagar responde 200."""
    res = client.get("/ap")
    assert res.status_code == 200


def test_ap_kpis():
    """Verificar endpoint de KPIs de Cuentas por Pagar."""
    res = client.get("/api/v1/ap/kpis", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "total_debt" in data
    assert "total_open_bills" in data
    assert "due_soon_count" in data
    assert "overdue_count" in data


def test_ap_bill_lifecycle_and_voucher():
    """Probar creación de factura, registro de abono, consulta de voucher y semáforos."""
    bill_number = f"FAC-TEST-{uuid.uuid4().hex[:6]}"
    
    # 1. Crear Factura de Proveedor
    create_res = client.post(
        "/api/v1/ap/bills",
        json={
            "supplier_name": "Proveedor Test S.A.",
            "supplier_nit": "1234567-8",
            "number": bill_number,
            "credit_days": 15,
            "total": 1500.00,
            "notes": "Compra de insumos de prueba"
        },
        headers=headers
    )
    assert create_res.status_code == 200
    bill_data = create_res.json()
    bill_id = bill_data["id"]
    assert bill_data["balance"] == 1500.00

    # 2. Listar Facturas y verificar que aparece
    list_res = client.get(f"/api/v1/ap/bills?search={bill_number}", headers=headers)
    assert list_res.status_code == 200
    bills = list_res.json()
    assert len(bills) >= 1
    target = next((b for b in bills if b["number"] == bill_number), None)
    assert target is not None
    assert target["balance"] == 1500.00

    # 3. Registrar Abono Parcial
    pmt_res = client.post(
        f"/api/v1/ap/bills/{bill_id}/payments",
        json={
            "amount": 500.00,
            "payment_method": "transfer",
            "reference": "TRANSF-001",
            "notes": "Primer abono"
        },
        headers=headers
    )
    assert pmt_res.status_code == 200
    pmt_data = pmt_res.json()
    assert pmt_data["paid_amount"] == 500.00
    assert pmt_data["new_balance"] == 1000.00
    assert "payment_id" in pmt_data
    payment_id = pmt_data["payment_id"]

    # 4. Consultar Voucher de Pago
    voucher_res = client.get(f"/api/v1/ap/payments/{payment_id}/voucher", headers=headers)
    assert voucher_res.status_code == 200
    voucher_data = voucher_res.json()
    assert voucher_data["amount_paid"] == 500.00
    assert voucher_data["bill"]["number"] == bill_number
    assert "voucher_number" in voucher_data

    # 5. Consultar Detalle con Historial
    detail_res = client.get(f"/api/v1/ap/bills/{bill_id}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["balance"] == 1000.00
    assert len(detail["payments"]) >= 1


def test_ap_aging_report():
    """Verificar endpoint de Antigüedad de Saldos."""
    res = client.get("/api/v1/ap/aging", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert "suppliers" in data
    assert "current" in data["summary"]
    assert "range_1_30" in data["summary"]
    assert "range_31_60" in data["summary"]
    assert "range_61_90" in data["summary"]
    assert "range_90_plus" in data["summary"]


def test_ap_export_csv():
    """Verificar exportación de cuentas por pagar en formato CSV."""
    res = client.get("/api/v1/ap/export", headers=headers)
    assert res.status_code == 200
    assert "text/csv" in res.headers.get("content-type", "")
    assert "attachment; filename=" in res.headers.get("content-disposition", "")
    assert "Proveedor,NIT,No. Factura" in res.text


def test_purchase_automatically_appears_in_ap():
    """Verificar que cualquier compra registrada aparece automáticamente en Cuentas por Pagar."""
    # 1. Crear Proveedor
    supp_res = client.post("/api/v1/partners/", json={
        "name": f"Proveedor CxP Auto {uuid.uuid4().hex[:4]}",
        "partner_type": "supplier",
        "nit": "8877665-4"
    }, headers=headers)
    assert supp_res.status_code == 200
    supplier_id = supp_res.json()["id"]

    # 2. Crear Producto
    prod_res = client.post("/api/v1/products/", json={
        "name": f"Producto CxP Test {uuid.uuid4().hex[:4]}",
        "sale_price": 50.0,
        "cost_price": 30.0,
        "product_type": "product",
        "supplier_id": supplier_id
    }, headers=headers)
    assert prod_res.status_code == 200
    product_id = prod_res.json()["id"]

    # 3. Crear Orden de Compra
    factura_ref = f"FAC-PO-{uuid.uuid4().hex[:6].upper()}"
    po_res = client.post("/api/v1/purchases/", json={
        "supplier_id": supplier_id,
        "supplier_ref": factura_ref,
        "payment_terms": "credit_30",
        "lines": [
            {
                "product_id": product_id,
                "product_name": "Producto CxP Test",
                "qty": 10.0,
                "unit_price": 30.0,
                "tax_rate": 12.0
            }
        ]
    }, headers=headers)
    assert po_res.status_code == 200
    po_data = po_res.json()
    assert "bill_id" in po_data

    # 4. Verificar que aparece inmediatamente en Cuentas por Pagar (AP)
    bills_res = client.get(f"/api/v1/ap/bills?search={factura_ref}", headers=headers)
    assert bills_res.status_code == 200
    bills = bills_res.json()
    assert len(bills) >= 1
    found_bill = next((b for b in bills if b["number"] == factura_ref), None)
    assert found_bill is not None
    assert found_bill["balance"] > 0
    assert found_bill["status"] in ("on_time", "due_soon", "overdue")

