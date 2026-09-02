"""API v1 — Envíos, Rutas y Entregas a Domicilio (Delivery & Dispatch)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder

router = APIRouter(prefix="/delivery")


class DeliveryOrderIn(BaseModel):
    customer_name: str
    customer_phone: Optional[str] = None
    delivery_address: str
    delivery_type: str = "delivery"  # "delivery" or "pickup"
    driver_name: Optional[str] = None
    delivery_fee: Decimal = Decimal("0.00")
    total_amount: Decimal = Decimal("0.00")
    notes: Optional[str] = None
    items_summary: Optional[str] = None


class DeliveryStatusUpdateIn(BaseModel):
    status: str  # "pending", "in_route", "delivered", "cancelled"
    driver_name: Optional[str] = None


# In-memory session delivery tracker for demo & high-speed dispatch
_delivery_orders: List[Dict[str, Any]] = [
    {
        "id": "del-001",
        "order_number": "ENV-2026-001",
        "customer_name": "Ing. Mario Castañeda",
        "customer_phone": "+502 5544-3322",
        "delivery_address": "Diagonal 6, 12-42 Zona 10, Edificio Design Center, Of. 801",
        "delivery_type": "delivery",
        "driver_name": "Carlos Méndez (Motorista 1)",
        "delivery_fee": 25.0,
        "total_amount": 340.0,
        "status": "in_route",
        "created_at": "10:15",
        "notes": "Llamar al llegar a garita",
        "items_summary": "1x Canasta de Frutas, 2x Jugo Natural"
    },
    {
        "id": "del-002",
        "order_number": "ENV-2026-002",
        "customer_name": "Lucía Morales",
        "customer_phone": "+502 4433-2211",
        "delivery_address": "Km 14.5 Carretera a El Salvador, Condominio Los Eucaliptos Casa 12",
        "delivery_type": "delivery",
        "driver_name": "Juan Ramos (Motorista 2)",
        "delivery_fee": 35.0,
        "total_amount": 580.0,
        "status": "pending",
        "created_at": "11:30",
        "notes": "Paga en efectivo contra entrega (Llevar cambio de Q200)",
        "items_summary": "5x Panadería Variada, 1x Pastel Red Velvet"
    },
    {
        "id": "del-003",
        "order_number": "ENV-2026-003",
        "customer_name": "Restaurante La Cabaña",
        "customer_phone": "+502 2211-0099",
        "delivery_address": "Recoger en Tienda (Pickup)",
        "delivery_type": "pickup",
        "driver_name": "Cliente recoge en mostrador",
        "delivery_fee": 0.0,
        "total_amount": 1250.0,
        "status": "delivered",
        "created_at": "09:00",
        "notes": "Listo empacado para recoger",
        "items_summary": "10x Harina Extra Fina 50lb"
    }
]


@router.get("/orders")
async def list_delivery_orders(
    status: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    """Listar órdenes de despacho y delivery."""
    if status:
        return [o for o in _delivery_orders if o["status"] == status]
    return _delivery_orders


@router.post("/orders")
async def create_delivery_order(
    data: DeliveryOrderIn,
    current_user=Depends(get_current_user),
):
    """Registrar nuevo pedido de despacho / entrega a domicilio."""
    seq = len(_delivery_orders) + 1
    new_order = {
        "id": f"del-{uuid.uuid4().hex[:6]}",
        "order_number": f"ENV-2026-{seq:03d}",
        "customer_name": data.customer_name,
        "customer_phone": data.customer_phone or "N/A",
        "delivery_address": data.delivery_address,
        "delivery_type": data.delivery_type,
        "driver_name": data.driver_name or "Sin asignar",
        "delivery_fee": float(data.delivery_fee),
        "total_amount": float(data.total_amount),
        "status": "pending",
        "created_at": datetime.now().strftime("%H:%M"),
        "notes": data.notes or "",
        "items_summary": data.items_summary or "Artículos varios"
    }
    _delivery_orders.insert(0, new_order)
    return new_order


@router.put("/orders/{order_id}/status")
async def update_delivery_status(
    order_id: str,
    data: DeliveryStatusUpdateIn,
    current_user=Depends(get_current_user),
):
    """Actualizar estado del envío (Preparación -> En Ruta -> Entregado)."""
    for o in _delivery_orders:
        if o["id"] == order_id:
            o["status"] = data.status
            if data.driver_name:
                o["driver_name"] = data.driver_name
            return o
    raise HTTPException(status_code=404, detail="Orden de envío no encontrada")


@router.get("/kpis")
async def get_delivery_kpis(
    current_user=Depends(get_current_user),
):
    """Obtener métricas y KPIs del módulo de entregas."""
    pending = sum(1 for o in _delivery_orders if o["status"] == "pending")
    in_route = sum(1 for o in _delivery_orders if o["status"] == "in_route")
    delivered = sum(1 for o in _delivery_orders if o["status"] == "delivered")
    total_delivered_val = sum(o["total_amount"] for o in _delivery_orders if o["status"] == "delivered")

    return {
        "pending": pending,
        "in_route": in_route,
        "delivered": delivered,
        "total_orders": len(_delivery_orders),
        "total_delivered_val": float(total_delivered_val)
    }
