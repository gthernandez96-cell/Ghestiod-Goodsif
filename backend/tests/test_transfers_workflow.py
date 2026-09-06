"""Pruebas unitarias para el flujo completo de Traslados Internos entre Bodegas."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select
import uuid
from decimal import Decimal

from app.main import app
from app.core.database import engine, Base
from app.core.security import create_access_token
from app.models import Company, User, Branch, Warehouse, Product
from app.models.inventory import StockQuant, StockMove, StockMoveType
from app.models.transfer import Transfer, TransferState

client = TestClient(app)

COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

token = create_access_token(data={"sub": str(USER_ID), "email": "test_admin@empresa.com", "company_id": str(COMPANY_ID)})
headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    import asyncio
    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            comp_existing = await session.execute(select(Company).where(Company.id == COMPANY_ID))
            if not comp_existing.scalar_one_or_none():
                comp = Company(id=COMPANY_ID, name="Empresa Test Transfers", nit="999888777")
                session.add(comp)
                await session.flush()

            # Asegurar 2 bodegas
            b_existing = await session.execute(select(Branch).where(Branch.company_id == COMPANY_ID))
            branch = b_existing.scalars().first()
            if not branch:
                branch = Branch(company_id=COMPANY_ID, name="Sucursal Central", code="SUC-TR-01", is_main=True)
                session.add(branch)
                await session.flush()

            wh1 = Warehouse(branch_id=branch.id, name="Bodega Origen TR", code="WH-ORIG-01", is_default=True)
            wh2 = Warehouse(branch_id=branch.id, name="Bodega Destino TR", code="WH-DEST-01", is_default=False)
            session.add_all([wh1, wh2])
            await session.commit()

    asyncio.run(_init())


def test_transfers_workflow_complete():
    # 1. Obtener bodegas disponibles
    wh_res = client.get("/api/v1/branches/warehouses", headers=headers)
    assert wh_res.status_code == 200
    warehouses = wh_res.json()
    assert len(warehouses) >= 2
    orig_wh_id = warehouses[0]["id"]
    dest_wh_id = warehouses[1]["id"]

    # 2. Crear un producto con stock inicial en bodega de origen
    prod_res = client.post("/api/v1/products/", json={
        "name": f"Producto Traslado Test {uuid.uuid4().hex[:6]}",
        "sale_price": 50.0,
        "cost_price": 30.0,
        "product_type": "PRODUCT",
        "tracking": "NONE"
    }, headers=headers)
    assert prod_res.status_code in (200, 201)
    prod_data = prod_res.json()
    prod_id = prod_data["id"]

    # Inyectar stock de 15 unidades con costo 30.0 en bodega de origen
    import asyncio
    async def _add_stock():
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            quant = StockQuant(
                product_id=uuid.UUID(prod_id),
                warehouse_id=uuid.UUID(orig_wh_id),
                qty_on_hand=Decimal("15.0"),
                qty_reserved=Decimal("0"),
                avg_cost=Decimal("30.0")
            )
            session.add(quant)
            await session.commit()
    asyncio.run(_add_stock())

    # 3. Consultar existencias vía endpoint /transfers/warehouse-stock
    stock_chk = client.get(f"/api/v1/transfers/warehouse-stock?warehouse_id={orig_wh_id}&product_ids={prod_id}", headers=headers)
    assert stock_chk.status_code == 200
    stock_data = stock_chk.json()
    assert prod_id in stock_data
    assert stock_data[prod_id]["qty_on_hand"] == 15.0
    assert stock_data[prod_id]["avg_cost"] == 30.0

    # 4. Crear un traslado solicitando 10 unidades
    create_res = client.post("/api/v1/transfers/", json={
        "origin_warehouse_id": orig_wh_id,
        "dest_warehouse_id": dest_wh_id,
        "notes": "Reabastecimiento de prueba",
        "lines": [{
            "product_id": prod_id,
            "product_name": prod_data["name"],
            "qty": 10.0
        }]
    }, headers=headers)
    assert create_res.status_code == 200
    tr_info = create_res.json()
    tr_id = tr_info["id"]

    # 5. Intentar despachar MÁS de lo disponible (ej. 20 unidades) -> debe fallar con HTTP 400
    fail_send = client.post(f"/api/v1/transfers/{tr_id}/send", json={
        "lines": [{
            "product_id": prod_id,
            "qty_sent": 20.0
        }]
    }, headers=headers)
    assert fail_send.status_code == 400
    assert "Stock insuficiente" in fail_send.json()["detail"]

    # 6. Despachar las 10 unidades válidas
    send_res = client.post(f"/api/v1/transfers/{tr_id}/send", json={
        "lines": [{
            "product_id": prod_id,
            "qty_sent": 10.0
        }],
        "notes": "Despacho camión 4"
    }, headers=headers)
    assert send_res.status_code == 200
    assert send_res.json()["state"] == "sent"

    # Verificar que en origen quedan 5 unidades (15 - 10)
    stock_after_send = client.get(f"/api/v1/transfers/warehouse-stock?warehouse_id={orig_wh_id}&product_ids={prod_id}", headers=headers)
    assert stock_after_send.json()[prod_id]["qty_on_hand"] == 5.0

    # 7. Recepción física con discrepancia (llegan 8 unidades físicamente, 2 faltantes)
    recv_res = client.post(f"/api/v1/transfers/{tr_id}/receive", json={
        "is_final": True,
        "notes": "Recibido con avería de 2 cajas",
        "lines": [{
            "product_id": prod_id,
            "qty_received": 8.0,
            "notes": "2 unidades rotas durante el transporte"
        }]
    }, headers=headers)
    assert recv_res.status_code == 200
    assert recv_res.json()["state"] == "received"

    # 8. Consultar detalle del traslado y verificar cálculo de discrepancia y costo
    get_tr = client.get(f"/api/v1/transfers/{tr_id}", headers=headers)
    assert get_tr.status_code == 200
    tr_detail = get_tr.json()
    assert tr_detail["lines"][0]["qty_sent"] == 10.0
    assert tr_detail["lines"][0]["qty_received"] == 8.0
    assert tr_detail["lines"][0]["qty_diff"] == 2.0
    assert tr_detail["lines"][0]["unit_cost"] == 30.0
    assert "rotas" in tr_detail["lines"][0]["notes"]

    # 9. Verificar que en destino se agregaron exactamente 8 unidades (no 10)
    dest_stock = client.get(f"/api/v1/transfers/warehouse-stock?warehouse_id={dest_wh_id}&product_ids={prod_id}", headers=headers)
    assert dest_stock.json()[prod_id]["qty_on_hand"] == 8.0
    assert dest_stock.json()[prod_id]["avg_cost"] == 30.0


def test_transfer_cancellation():
    wh_res = client.get("/api/v1/branches/warehouses", headers=headers)
    warehouses = wh_res.json()
    orig_wh_id = warehouses[0]["id"]
    dest_wh_id = warehouses[1]["id"]

    # Crear traslado borrador / confirmado
    create_res = client.post("/api/v1/transfers/", json={
        "origin_warehouse_id": orig_wh_id,
        "dest_warehouse_id": dest_wh_id,
        "notes": "Traslado a cancelar",
        "lines": [{
            "product_id": str(uuid.uuid4()),
            "product_name": "Producto Demo",
            "qty": 5.0
        }]
    }, headers=headers)
    assert create_res.status_code == 200
    tr_id = create_res.json()["id"]

    # Cancelar traslado
    cancel_res = client.post(f"/api/v1/transfers/{tr_id}/cancel", json={
        "reason": "Cliente canceló el pedido especial"
    }, headers=headers)
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"

    # Verificar que el estado cambió a cancelled
    get_tr = client.get(f"/api/v1/transfers/{tr_id}", headers=headers)
    assert get_tr.json()["state"] == "cancelled"
    assert "Cliente canceló" in get_tr.json()["rejection_reason"]
