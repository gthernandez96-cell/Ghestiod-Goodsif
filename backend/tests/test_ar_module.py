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

        # 3. Export individual statement CSV
        exp_res = client.get(f"/api/v1/ar/customers/{cust_id}/export", headers=headers)
        assert exp_res.status_code == 200
        assert "text/csv" in exp_res.headers.get("content-type", "")
        assert "attachment; filename=" in exp_res.headers.get("content-disposition", "")

        # 4. Export individual statement Excel (.xlsx)
        excel_res = client.get(f"/api/v1/ar/customers/{cust_id}/export-excel", headers=headers)
        assert excel_res.status_code == 200
        assert "spreadsheetml.sheet" in excel_res.headers.get("content-type", "")
        assert "attachment; filename=" in excel_res.headers.get("content-disposition", "")
        assert len(excel_res.content) > 5000

        # 5. Export individual statement PDF (.pdf)
        pdf_res = client.get(f"/api/v1/ar/customers/{cust_id}/export-pdf", headers=headers)
        assert pdf_res.status_code == 200
        assert "application/pdf" in pdf_res.headers.get("content-type", "")
        assert "attachment; filename=" in pdf_res.headers.get("content-disposition", "")
        assert pdf_res.content.startswith(b"%PDF-")
        assert len(pdf_res.content) > 2000


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


def test_partner_sales_separation_cash_credit():
    """Verificar que el historial de ventas del socio separa ventas en efectivo y a crédito."""
    import uuid
    uid = uuid.uuid4().hex[:6]

    # 1. Crear cliente con crédito
    cust_res = client.post("/api/v1/partners/", json={
        "name": f"Cliente Separacion Test {uid}",
        "nit": f"{uid}-1",
        "partner_type": "customer",
        "credit_limit": 1000.0,
    }, headers=headers)
    assert cust_res.status_code == 200
    cust_id = cust_res.json()["id"]

    # 2. Obtener sucursal y bodega
    b_res = client.get("/api/v1/branches/", headers=headers)
    branch_id = b_res.json()[0]["id"]
    wh_res = client.get("/api/v1/warehouses/", headers=headers)
    wh_id = wh_res.json()[0]["id"] if wh_res.status_code == 200 and wh_res.json() else branch_id

    # 3. Crear producto
    p_res = client.post("/api/v1/products/", json={
        "name": f"Prod Sep {uid}",
        "price": 50.0,
        "cost_price": 25.0,
        "sku": f"SKU-{uid}",
        "initial_stock": 20.0,
    }, headers=headers)
    assert p_res.status_code == 200
    prod_id = p_res.json()["id"]

    # 4. Crear venta en efectivo (al contado)
    cash_sale = client.post("/api/v1/sales/", json={
        "customer_id": cust_id,
        "branch_id": branch_id,
        "warehouse_id": wh_id,
        "lines": [{
            "product_id": prod_id,
            "product_name": f"Prod Sep {uid}",
            "qty": 1.0,
            "unit_price": 50.0,
            "tax_rate": 0.0,
        }],
        "payments": [{
            "payment_method": "cash",
            "amount": 50.0,
        }]
    }, headers=headers)
    assert cash_sale.status_code == 200

    # 5. Crear venta al crédito (pendiente de pago, saldo = 100)
    credit_sale = client.post("/api/v1/sales/", json={
        "customer_id": cust_id,
        "branch_id": branch_id,
        "warehouse_id": wh_id,
        "sale_type": "credit",
        "lines": [{
            "product_id": prod_id,
            "product_name": f"Prod Sep {uid}",
            "qty": 2.0,
            "unit_price": 50.0,
            "tax_rate": 0.0,
        }],
        "payments": []
    }, headers=headers)
    assert credit_sale.status_code == 200

    # 6. Consultar todas las ventas del cliente
    res_all = client.get(f"/api/v1/partners/{cust_id}/sales", headers=headers)
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["total"] == 2
    assert "summary" in data_all

    summary = data_all["summary"]
    assert summary["count_cash"] == 1
    assert summary["count_credit"] == 1
    assert summary["total_cash"] == 50.0
    assert summary["total_credit"] == 100.0
    assert summary["total_credit_due"] == 100.0

    # Validar atributos de cada item
    items = data_all["items"]
    cash_item = next(it for it in items if not it["is_credit"])
    credit_item = next(it for it in items if it["is_credit"])

    assert cash_item["payment_type"] == "cash"
    assert cash_item["payment_type_label"] == "Efectivo"
    assert cash_item["total"] == 50.0

    assert credit_item["payment_type"] == "credit"
    assert credit_item["payment_type_label"] == "Crédito"
    assert credit_item["total"] == 100.0
    assert credit_item["amount_due"] == 100.0

    # 7. Consultar filtrando solo efectivo
    res_cash_only = client.get(f"/api/v1/partners/{cust_id}/sales?payment_type=cash", headers=headers)
    assert res_cash_only.status_code == 200
    assert res_cash_only.json()["total"] == 1
    assert res_cash_only.json()["items"][0]["payment_type"] == "cash"

    # 8. Consultar filtrando solo crédito
    res_credit_only = client.get(f"/api/v1/partners/{cust_id}/sales?payment_type=credit", headers=headers)
    assert res_credit_only.status_code == 200
    assert res_credit_only.json()["total"] == 1
    assert res_credit_only.json()["items"][0]["payment_type"] == "credit"


def test_multi_company_statement_and_receipt_formatting():
    """Verificar que en un entorno multi-empresa, cada empresa genera su propio estado de cuenta y recibo con su membrete y logotipos."""
    # 1. Crear una segunda empresa en el sistema vía API
    unique_nit = f"NIT-{uuid.uuid4().hex[:6]}"
    c_create_res = client.post("/api/v1/companies", json={
        "name": "Pinturas & Ferretería El Roble S.A.",
        "trade_name": "El Roble Express",
        "nit": unique_nit,
        "address": "Km 45 Carretera Interamericana, Chimaltenango",
        "phone": "+502 7766-5544",
        "email": "cobros@elroble.gt",
        "currency": "GTQ",
        "logo_url": "https://elroble.gt/logo.png"
    }, headers=headers)
    assert c_create_res.status_code == 200
    comp_b_id = c_create_res.json()["id"]

    try:
        # Cambiar el usuario actual a la Empresa B
        sw_res = client.post(f"/api/v1/companies/switch/{comp_b_id}", headers=headers)
        assert sw_res.status_code == 200

        # 2. Configurar membrete y formatos específicos de la Empresa B
        up_settings_res = client.put("/api/v1/companies/current", json={
            "receipt_title": "Pinturas & Ferretería El Roble",
            "receipt_emails": "pagos@elroble.gt",
            "receipt_phone": "+502 7766-5544",
            "receipt_address": "Chimaltenango, Guatemala",
            "receipt_logo_left": "https://elroble.gt/logo_left.png",
            "receipt_logo_right": "https://elroble.gt/sello.png",
            "statement_banner_url": "https://elroble.gt/banner.png",
            "receipt_footer_notes": "El Roble — Siempre a su servicio.",
            "is_official_goodsif": False
        }, headers=headers)
        assert up_settings_res.status_code == 200

        # 3. Crear cliente para Empresa B
        c_res = client.post("/api/v1/partners/", json={
            "name": "Constructora Los Volcanes S.A.",
            "nit": "1234000-9",
            "partner_type": "customer",
            "credit_limit": 80000.0,
            "credit_days": 45
        }, headers=headers)
        assert c_res.status_code == 200
        cust_b_id = c_res.json()["id"]

        # 4. Crear factura al crédito para Empresa B
        inv_res = client.post("/api/v1/ar/invoices", json={
            "customer_id": cust_b_id,
            "number": f"FAC-ROBLE-{uuid.uuid4().hex[:4]}",
            "total": 12000.0,
            "days": 45,
            "notes": "Venta al crédito de materiales"
        }, headers=headers)
        assert inv_res.status_code == 200
        inv_b_id = inv_res.json()["id"]

        # 5. Registrar abono para Empresa B
        pmt_res = client.post(f"/api/v1/ar/invoices/{inv_b_id}/payments", json={
            "amount": 4000.0,
            "payment_method": "transfer",
            "reference": "TRANSF-8899",
            "notes": "Abono inicial transferencia bancaria"
        }, headers=headers)
        assert pmt_res.status_code == 200
        pmt_b_id = pmt_res.json()["payment_id"]

        # 6. Consultar Recibo Media Carta para Empresa B
        rcpt_res = client.get(f"/api/v1/ar/payments/{pmt_b_id}/receipt", headers=headers)
        assert rcpt_res.status_code == 200
        rcpt = rcpt_res.json()

        # Verificar que el recibo de Empresa B tiene sus propios datos, NO los de Productos Automotrices del Sur
        assert rcpt["amount_paid"] == 4000.0
        assert "CUATRO MIL QUETZALES EXACTOS" in rcpt["amount_letters"]
        assert rcpt["company"]["name"] == "Pinturas & Ferretería El Roble S.A."
        assert rcpt["company"]["title_name"] == "Pinturas & Ferretería El Roble"
        assert rcpt["company"]["emails"] == "pagos@elroble.gt"
        assert rcpt["company"]["phone"] == "+502 7766-5544"
        assert rcpt["company"]["logo_left"] == "https://elroble.gt/logo_left.png"
        assert rcpt["company"]["logo_right"] == "https://elroble.gt/sello.png"
        assert rcpt["company"]["is_official_goodsif"] is False
        assert rcpt["company"]["footer_notes"] == "El Roble — Siempre a su servicio."

        # 7. Consultar Estado de Cuenta de Empresa B
        st_res = client.get(f"/api/v1/ar/customers/{cust_b_id}/statement", headers=headers)
        assert st_res.status_code == 200
        st_data = st_res.json()
        assert st_data["company"]["name"] == "Pinturas & Ferretería El Roble S.A."
        assert st_data["company"]["statement_banner_url"] == "https://elroble.gt/banner.png"
        assert st_data["company"]["is_official_goodsif"] is False
        assert st_data["summary"]["total_envio"] == 12000.0
        assert st_data["summary"]["total_abono"] == 4000.0
        assert st_data["summary"]["total_deuda"] == 8000.0

        # 8. Exportar Estado de Cuenta Excel (.xlsx) y PDF (.pdf) de Empresa B
        excel_res = client.get(f"/api/v1/ar/customers/{cust_b_id}/export-excel", headers=headers)
        assert excel_res.status_code == 200
        assert len(excel_res.content) > 4000

        pdf_res = client.get(f"/api/v1/ar/customers/{cust_b_id}/export-pdf", headers=headers)
        assert pdf_res.status_code == 200
        assert pdf_res.content.startswith(b"%PDF-")
        assert len(pdf_res.content) > 1500

        # 9. Modificar la configuración de Empresa B mediante el endpoint de Settings y verificar persistencia
        up_res = client.put("/api/v1/companies/current", json={
            "receipt_title": "El Roble Ferreterías - Central",
            "receipt_phone": "+502 2222-3333",
            "is_official_goodsif": False
        }, headers=headers)
        assert up_res.status_code == 200

        # Verificar que el endpoint de settings devuelve los nuevos datos
        get_st = client.get("/api/v1/companies/current", headers=headers)
        assert get_st.status_code == 200
        assert get_st.json()["receipt_title"] == "El Roble Ferreterías - Central"
        assert get_st.json()["receipt_phone"] == "+502 2222-3333"

        # 10. Consultar branding activo de Cotizaciones para Empresa B
        q_brand = client.get("/api/v1/quotes/branding/current", headers=headers)
        assert q_brand.status_code == 200
        qb = q_brand.json()
        assert qb["name"] == "Pinturas & Ferretería El Roble S.A."
        assert qb["title_name"] == "El Roble Ferreterías - Central"
        assert qb["phone"] == "+502 2222-3333"
        assert qb["is_official_goodsif"] is False

        # 11. Crear una cotización para Empresa B y verificar membrete devuelto
        q_create = client.post("/api/v1/quotes/", json={
            "customer_name": "Constructora Los Laureles",
            "customer_nit": "9876543-2",
            "customer_email": "compras@laureles.com",
            "condition": "Crédito 30 días",
            "lines": [
                {
                    "product_id": str(uuid.uuid4()),
                    "product_name": "Esmalte Anticorrosivo Gris Galón",
                    "product_code": "PNT-001",
                    "uom_name": "GAL",
                    "qty": 5.0,
                    "unit_price": 140.0
                }
            ]
        }, headers=headers)
        assert q_create.status_code == 200
        q_b_id = q_create.json()["id"]

        # Consultar cotización y verificar branding específico de Empresa B
        q_b_detail = client.get(f"/api/v1/quotes/{q_b_id}", headers=headers)
        assert q_b_detail.status_code == 200
        q_data = q_b_detail.json()
        assert q_data["company"]["name"] == "Pinturas & Ferretería El Roble S.A."
        assert q_data["company"]["title_name"] == "El Roble Ferreterías - Central"
        assert q_data["company"]["is_official_goodsif"] is False
        assert q_data["company_info"]["name"] == "El Roble Ferreterías - Central"

        # 12. Enviar email de cotización de Empresa B y verificar que NO incluye Productos Automotrices del Sur
        q_email = client.post(f"/api/v1/quotes/{q_b_id}/send-email", json={
            "recipient_email": "compras@laureles.com",
            "message": "Favor revisar cotización de esmalte"
        }, headers=headers)
        assert q_email.status_code == 200
        if "mailto_url" in q_email.json():
            mailto = q_email.json()["mailto_url"]
            assert "EL%20ROBLE" in mailto or "El%20Roble" in mailto
            assert "PRODUCTOS%20AUTOMOTRICES" not in mailto

    finally:
        # Restaurar usuario a COMPANY_ID original
        client.post(f"/api/v1/companies/switch/{COMPANY_ID}", headers=headers)

