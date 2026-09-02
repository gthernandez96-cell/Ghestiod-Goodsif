"""Pruebas para el módulo de Cuentas por Cobrar (AR), Créditos a Clientes y Analítica del Dashboard."""
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


def test_ar_page_route():
    """Verificar que la vista HTML de Cuentas por Cobrar responde 200."""
    res = client.get("/ar")
    assert res.status_code == 200


def test_ar_kpis():
    """Verificar endpoint de KPIs de Cuentas por Cobrar."""
    res = client.get("/api/v1/ar/kpis", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "total_receivable" in data
    assert "total_open_invoices" in data
    assert "due_soon_count" in data
    assert "overdue_count" in data
    assert "collected_this_month" in data


def test_ar_credit_invoice_lifecycle_and_receipt():
    """Probar creación de cuenta por cobrar, registro de abono, consulta de recibo y detalle."""
    inv_number = f"FAC-AR-TEST-{uuid.uuid4().hex[:6]}"

    # 1. Crear Cuenta por Cobrar
    create_res = client.post(
        "/api/v1/ar/invoices",
        json={
            "customer_name": "Cliente Crédito VIP S.A.",
            "customer_nit": "9988776-5",
            "number": inv_number,
            "credit_days": 30,
            "total": 2400.00,
            "notes": "Venta de mercadería a crédito 30 días"
        },
        headers=headers
    )
    assert create_res.status_code == 200
    inv_data = create_res.json()
    invoice_id = inv_data["id"]
    assert inv_data["balance"] == 2400.00

    # 2. Listar y buscar la cuenta creada
    list_res = client.get(f"/api/v1/ar/invoices?search={inv_number}", headers=headers)
    assert list_res.status_code == 200
    invoices = list_res.json()
    assert len(invoices) >= 1
    target = next((i for i in invoices if i["number"] == inv_number), None)
    assert target is not None
    assert target["balance"] == 2400.00

    # 3. Registrar Abono Parcial
    pmt_res = client.post(
        f"/api/v1/ar/invoices/{invoice_id}/payments",
        json={
            "amount": 900.00,
            "payment_method": "transfer",
            "reference": "TRANSF-REC-883",
            "notes": "Primer abono del cliente"
        },
        headers=headers
    )
    assert pmt_res.status_code == 200
    pmt_data = pmt_res.json()
    assert pmt_data["paid_amount"] == 900.00
    assert pmt_data["new_balance"] == 1500.00
    assert "payment_id" in pmt_data
    payment_id = pmt_data["payment_id"]
    assert "receipt_number" in pmt_data

    # 4. Consultar Comprobante de Recibo
    receipt_res = client.get(f"/api/v1/ar/payments/{payment_id}/receipt", headers=headers)
    assert receipt_res.status_code == 200
    receipt_data = receipt_res.json()
    assert receipt_data["amount_paid"] == 900.00
    assert receipt_data["invoice"]["number"] == inv_number
    assert "receipt_number" in receipt_data

    # 5. Consultar Detalle con Historial
    detail_res = client.get(f"/api/v1/ar/invoices/{invoice_id}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["balance"] == 1500.00
    assert len(detail["payments"]) >= 1


def test_ar_aging_report():
    """Verificar reporte de Antigüedad de Saldos por cliente."""
    res = client.get("/api/v1/ar/aging", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert "customers" in data
    assert "current" in data["summary"]
    assert "range_1_30" in data["summary"]
    assert "range_31_60" in data["summary"]
    assert "range_61_90" in data["summary"]
    assert "range_90_plus" in data["summary"]


def test_ar_export_csv():
    """Verificar exportación de cuentas por cobrar en formato CSV."""
    res = client.get("/api/v1/ar/export", headers=headers)
    assert res.status_code == 200
    assert "text/csv" in res.headers.get("content-type", "")
    assert "attachment; filename=" in res.headers.get("content-disposition", "")
    assert "Cliente,NIT,No. Factura" in res.text


def test_ar_customers_lookup_and_statement_export():
    """Verificar búsqueda de clientes para autocompletado y exportación de estado de cuenta individual."""
    # 1. Lookup
    res = client.get("/api/v1/ar/customers-lookup", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert isinstance(data["items"], list)

    if len(data["items"]) > 0:
        cust_id = data["items"][0]["id"]
        # 2. Statement
        st_res = client.get(f"/api/v1/ar/customers/{cust_id}/statement", headers=headers)
        assert st_res.status_code == 200
        st_data = st_res.json()
        assert "customer" in st_data
        assert "invoices" in st_data
        assert "payments" in st_data

        # 3. Export individual statement
        exp_res = client.get(f"/api/v1/ar/customers/{cust_id}/export", headers=headers)
        assert exp_res.status_code == 200
        assert "text/csv" in exp_res.headers.get("content-type", "")
        assert "attachment; filename=" in exp_res.headers.get("content-disposition", "")


def test_dashboard_analytics_endpoints():
    """Verificar que todos los endpoints del Dashboard Analítico responden con estructura válida."""
    # 1. KPIs
    res_kpis = client.get("/api/v1/dashboard/kpis", headers=headers)
    assert res_kpis.status_code == 200
    kpis = res_kpis.json()
    assert "sales_today" in kpis
    assert "sales_month" in kpis
    assert "avg_ticket_today" in kpis
    assert "total_receivable" in kpis
    assert "total_payable" in kpis

    # 2. Sales Chart
    res_chart = client.get("/api/v1/dashboard/sales-chart?days=7", headers=headers)
    assert res_chart.status_code == 200
    chart = res_chart.json()
    assert "labels" in chart
    assert "data" in chart

    # 3. Category Breakdown
    res_cat = client.get("/api/v1/dashboard/category-breakdown", headers=headers)
    assert res_cat.status_code == 200
    cat = res_cat.json()
    assert "labels" in cat
    assert "data" in cat

    # 4. Payment Methods
    res_pm = client.get("/api/v1/dashboard/payment-methods", headers=headers)
    assert res_pm.status_code == 200
    pm = res_pm.json()
    assert "labels" in pm
    assert "data" in pm

    # 5. Peak Hours
    res_hours = client.get("/api/v1/dashboard/sales-by-hour", headers=headers)
    assert res_hours.status_code == 200
    hours = res_hours.json()
    assert "labels" in hours
    assert "sales" in hours
