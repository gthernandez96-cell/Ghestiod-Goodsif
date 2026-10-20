"""API v1 — Módulo de Fabricación y Ensamblaje de Productos (Multi-Bodega y Atómico)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone
import uuid
import logging
import re

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.mfg import (
    RecetaFavorita, DetalleReceta,
    OrdenFabricacion, DetalleFabricacion
)
from app.models.product import Product, ProductCategory, ProductType
from app.models.company import Warehouse, Branch
from app.models.inventory import StockMoveType, StockQuant, StockMove
from app.services.inventory_service import InventoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mfg")


# ============================================================================
# SCHEMAS (Pydantic v2)
# ============================================================================

class InsumoFabricacionIn(BaseModel):
    """Insumo individual con su bodega de origen específica."""
    producto_id: str = Field(..., description="ID del producto insumo/materia prima")
    bodega_origen_id: str = Field(..., description="ID de la bodega de donde saldrá este insumo")
    cantidad: Decimal = Field(..., gt=0, description="Cantidad a consumir del insumo")


class ProductoNuevoIn(BaseModel):
    """Datos para crear un nuevo producto terminado resultante de la fabricación."""
    nombre: str = Field(..., description="Nombre del nuevo producto a fabricar")
    codigo_barras: Optional[str] = Field(None, description="Código de barras opcional")
    correlativo_interno: Optional[str] = Field(None, description="Correlativo interno (SKU) del sistema")
    categoria_id: Optional[str] = Field(None, description="ID de la categoría del producto")


class FabricacionIn(BaseModel):
    """Payload para ejecutar la fabricación/fusión de productos."""
    # Soporta producto nuevo O producto existente
    producto_nuevo: Optional[ProductoNuevoIn] = Field(None, description="Datos si es un producto nuevo")
    producto_final_id: Optional[str] = Field(None, description="ID si es un producto ya existente")

    bodega_destino_id: str = Field(..., description="ID obligatorio de la bodega destino donde ingresará el producto final")
    cantidad_producida: Decimal = Field(..., gt=0, description="Cantidad de producto final terminada")
    precio_venta: Decimal = Field(..., gt=0, description="Precio de venta confirmado para el producto final")
    insumos: List[InsumoFabricacionIn] = Field(..., min_length=2, description="Lista de al menos 2 insumos de materia prima")
    guardar_como_favorito: bool = Field(False, description="Guardar esta fórmula como receta favorita")
    nombre_receta: Optional[str] = Field(None, description="Nombre personalizado si se guarda como favorita")
    notas: Optional[str] = Field(None, description="Notas u observaciones de la fabricación")


class RecetaFavoritaIn(BaseModel):
    """Creación/actualización manual de una receta favorita."""
    nombre: str
    producto_final_id: str
    bodega_destino_id: Optional[str] = None
    cantidad_producida: Decimal = Decimal("1.0")
    precio_venta_sugerido: Decimal = Decimal("0.0")
    insumos: List[InsumoFabricacionIn] = Field(..., min_length=2)


class WarehouseOut(BaseModel):
    id: str
    name: str
    code: str
    branch_name: str
    is_default: bool

    model_config = ConfigDict(from_attributes=True)


class CategoryMFGOut(BaseModel):
    id: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class ProductStockOut(BaseModel):
    id: str
    name: str
    internal_ref: Optional[str] = None
    barcode: Optional[str] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    cost_price: float = 0.0
    sale_price: float = 0.0
    stock_total: float = 0.0
    stock_by_warehouse: Dict[str, float] = {}

    model_config = ConfigDict(from_attributes=True)


class InsumoDetalleOut(BaseModel):
    id: str
    producto_id: str
    producto_nombre: str
    producto_ref: Optional[str] = None
    bodega_origen_id: str
    bodega_origen_nombre: str
    cantidad: float
    costo_unitario: float
    costo_subtotal: float

    model_config = ConfigDict(from_attributes=True)


class OrdenFabricacionOut(BaseModel):
    id: str
    numero: str
    producto_final_id: str
    producto_final_nombre: str
    producto_final_ref: Optional[str] = None
    producto_final_barcode: Optional[str] = None
    bodega_destino_id: str
    bodega_destino_nombre: str
    cantidad_producida: float
    costo_total: float
    costo_unitario: float
    precio_venta: float
    margen_estimado_pct: float
    estado: str
    fecha: str
    notas: Optional[str] = None
    insumos: List[InsumoDetalleOut] = []

    model_config = ConfigDict(from_attributes=True)


class DetalleRecetaOut(BaseModel):
    id: str
    producto_id: str
    producto_nombre: str
    bodega_origen_id: Optional[str] = None
    bodega_origen_nombre: Optional[str] = None
    cantidad: float
    costo_unitario_actual: float = 0.0
    stock_disponible_actual: float = 0.0

    model_config = ConfigDict(from_attributes=True)


class RecetaFavoritaOut(BaseModel):
    id: str
    nombre: str
    producto_final_id: str
    producto_final_nombre: str
    bodega_destino_id: Optional[str] = None
    bodega_destino_nombre: Optional[str] = None
    cantidad_producida: float
    precio_venta_sugerido: float
    costo_total_estimado: float
    insumos_count: int
    detalles: List[DetalleRecetaOut] = []

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# HELPERS
# ============================================================================

def _parse_uuid(val: Any, field_name: str = "ID") -> uuid.UUID:
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"{field_name} inválido: '{val}'")


def _generate_fabricacion_numero(seq: int) -> str:
    return f"FAB/{seq:05d}"


async def _get_next_product_sku(db: AsyncSession, company_id: uuid.UUID) -> str:
    """Genera el siguiente número correlativo interno para productos de la empresa."""
    stmt = select(Product.internal_ref).where(Product.company_id == company_id)
    res = await db.execute(stmt)
    existing_refs = [r for r in res.scalars().all() if r]

    max_num = 0
    for ref in existing_refs:
        matches = re.findall(r"\d+", str(ref))
        if matches:
            for m in matches:
                try:
                    val = int(m)
                    if val > max_num:
                        max_num = val
                except ValueError:
                    pass

    next_num = max_num + 1
    return f"PROD-{next_num:04d}"


# ============================================================================
# 1. BODEGAS, CATEGORÍAS Y PRODUCTOS
# ============================================================================

@router.get("/warehouses", response_model=List[WarehouseOut])
async def list_warehouses_mfg(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar bodegas activas de la compañía para selectores de origen y destino."""
    stmt = (
        select(Warehouse, Branch.name.label("branch_name"))
        .join(Branch, Warehouse.branch_id == Branch.id)
        .where(
            Branch.company_id == current_user.company_id,
            Warehouse.is_active == True,
            Warehouse.is_deleted == False
        )
        .order_by(Warehouse.is_default.desc(), Warehouse.name.asc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    output = []
    for wh, branch_name in rows:
        output.append(WarehouseOut(
            id=str(wh.id),
            name=wh.name,
            code=wh.code,
            branch_name=branch_name or "",
            is_default=bool(wh.is_default)
        ))
    return output


@router.get("/categories", response_model=List[CategoryMFGOut])
async def list_categories_mfg(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar categorías de productos de la empresa para asignar al nuevo producto fabricado."""
    stmt = (
        select(ProductCategory)
        .where(
            ProductCategory.company_id == current_user.company_id,
            ProductCategory.is_deleted == False,
            ProductCategory.is_active == True
        )
        .order_by(ProductCategory.name.asc())
    )
    res = await db.execute(stmt)
    cats = res.scalars().all()
    return [CategoryMFGOut(id=str(c.id), name=c.name) for c in cats]


@router.get("/next-sku")
async def get_next_sku_mfg(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener el siguiente correlativo interno sugerido para el nuevo producto."""
    sku = await _get_next_product_sku(db, current_user.company_id)
    return {"next_sku": sku}


@router.get("/products-stock", response_model=List[ProductStockOut])
async def list_products_stock_mfg(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar productos con su stock disponible discriminado por bodega."""
    stmt_p = (
        select(Product)
        .options(selectinload(Product.category))
        .where(
            Product.company_id == current_user.company_id,
            Product.is_deleted == False
        )
        .order_by(Product.name.asc())
    )
    res_p = await db.execute(stmt_p)
    products = res_p.scalars().all()

    # Quants activos
    stmt_q = (
        select(
            StockQuant.product_id,
            StockQuant.warehouse_id,
            func.coalesce(func.sum(StockQuant.qty_on_hand - StockQuant.qty_reserved), 0).label("stock")
        )
        .where(
            StockQuant.is_deleted == False,
            StockQuant.variant_id.is_(None)
        )
        .group_by(StockQuant.product_id, StockQuant.warehouse_id)
    )
    res_q = await db.execute(stmt_q)
    quants = res_q.all()

    stock_map: Dict[uuid.UUID, Dict[str, float]] = {}
    for p_id, wh_id, st in quants:
        if p_id not in stock_map:
            stock_map[p_id] = {}
        stock_map[p_id][str(wh_id)] = float(st)

    output = []
    for p in products:
        by_wh = stock_map.get(p.id, {})
        total_st = sum(by_wh.values())
        output.append(ProductStockOut(
            id=str(p.id),
            name=p.name,
            internal_ref=p.internal_ref or "",
            barcode=p.barcode or "",
            category_id=str(p.category_id) if p.category_id else None,
            category_name=p.category.name if p.category else None,
            cost_price=float(p.cost_price or 0),
            sale_price=float(p.sale_price or 0),
            stock_total=total_st,
            stock_by_warehouse=by_wh
        ))
    return output


# ============================================================================
# 2. RECETAS FAVORITAS (PLANTILLAS DE ENSAMBLAJE)
# ============================================================================

@router.get("/recetas-favoritas", response_model=List[RecetaFavoritaOut])
async def list_recetas_favoritas(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar recetas favoritas / plantillas guardadas con sus insumos y bodegas sugeridas."""
    stmt = (
        select(RecetaFavorita)
        .options(
            selectinload(RecetaFavorita.producto_final),
            selectinload(RecetaFavorita.bodega_destino),
            selectinload(RecetaFavorita.detalles).selectinload(DetalleReceta.producto),
            selectinload(RecetaFavorita.detalles).selectinload(DetalleReceta.bodega_origen),
        )
        .where(
            RecetaFavorita.company_id == current_user.company_id,
            RecetaFavorita.is_deleted == False,
            RecetaFavorita.is_active == True
        )
        .order_by(RecetaFavorita.created_at.desc())
    )
    res = await db.execute(stmt)
    recetas = res.scalars().all()

    stmt_q = (
        select(
            StockQuant.product_id,
            StockQuant.warehouse_id,
            func.coalesce(func.sum(StockQuant.qty_on_hand - StockQuant.qty_reserved), 0).label("stock")
        )
        .where(StockQuant.is_deleted == False, StockQuant.variant_id.is_(None))
        .group_by(StockQuant.product_id, StockQuant.warehouse_id)
    )
    res_q = await db.execute(stmt_q)
    quant_lookup = {(row.product_id, row.warehouse_id): float(row.stock) for row in res_q.all()}

    output = []
    for r in recetas:
        detalles_out = []
        for d in r.detalles:
            current_cost = float(d.producto.cost_price or 0) if d.producto else 0.0
            avail_stock = quant_lookup.get((d.producto_id, d.bodega_origen_id), 0.0) if d.bodega_origen_id else 0.0

            detalles_out.append(DetalleRecetaOut(
                id=str(d.id),
                producto_id=str(d.producto_id),
                producto_nombre=d.producto.name if d.producto else "Insumo",
                bodega_origen_id=str(d.bodega_origen_id) if d.bodega_origen_id else None,
                bodega_origen_nombre=d.bodega_origen.name if d.bodega_origen else None,
                cantidad=float(d.cantidad),
                costo_unitario_actual=current_cost,
                stock_disponible_actual=avail_stock
            ))

        output.append(RecetaFavoritaOut(
            id=str(r.id),
            nombre=r.nombre,
            producto_final_id=str(r.producto_final_id),
            producto_final_nombre=r.producto_final.name if r.producto_final else "Producto Desconocido",
            bodega_destino_id=str(r.bodega_destino_id) if r.bodega_destino_id else None,
            bodega_destino_nombre=r.bodega_destino.name if r.bodega_destino else None,
            cantidad_producida=float(r.cantidad_producida),
            precio_venta_sugerido=float(r.precio_venta_sugerido or 0),
            costo_total_estimado=float(r.costo_total_estimado or 0),
            insumos_count=len(r.detalles),
            detalles=detalles_out
        ))

    return output


@router.delete("/recetas-favoritas/{receta_id}")
async def delete_receta_favorita(
    receta_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Eliminar receta favorita."""
    r_uuid = _parse_uuid(receta_id, "ID de Receta")
    stmt = select(RecetaFavorita).where(
        RecetaFavorita.id == r_uuid,
        RecetaFavorita.company_id == current_user.company_id,
        RecetaFavorita.is_deleted == False
    )
    res = await db.execute(stmt)
    receta = res.scalar_one_or_none()
    if not receta:
        raise HTTPException(status_code=404, detail="Receta favorita no encontrada.")

    receta.is_deleted = True
    receta.is_active = False
    await db.commit()
    return {"status": "ok", "message": f"Receta '{receta.nombre}' eliminada con éxito."}


# ============================================================================
# 3. TRANSACCIÓN ATÓMICA DE FABRICACIÓN (PRODUCTO NUEVO + MULTI-BODEGA)
# ============================================================================

@router.post("/fabricar", response_model=OrdenFabricacionOut)
async def ejecutar_fabricacion(
    data: FabricacionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Ejecutar proceso atómico de fabricación/fusión:
    1. Si es producto nuevo, crea el producto con su nombre, código de barras, correlativo interno y categoría.
    2. Valida mínimo 2 insumos de materia prima.
    3. Valida Bodega de Destino obligatoria.
    4. Bloquea filas con select_for_update y valida stock suficiente por bodega de origen.
    5. Resta materias primas de sus respectivas bodegas de origen.
    6. Suma producto terminado en la bodega de destino (creando registro si no existe).
    7. Asigna costo (suma de los insumos) y precio de venta confirmado al producto terminado.
    8. Si guardar_como_favorito es True, crea/actualiza la receta favorita.
    9. Registra orden histórica y detalles en una sola transacción atómica.
    """
    # ---------------------------------------------------------
    # VALIDACIÓN 1: MÍNIMO 2 INSUMOS
    # ---------------------------------------------------------
    if not data.insumos or len(data.insumos) < 2:
        raise HTTPException(
            status_code=400,
            detail="Para fabricar un producto debes seleccionar al menos 2 productos insumo (materia prima)."
        )

    # VALIDACIÓN 2: CANTIDAD Y PRECIO DE VENTA
    if data.cantidad_producida <= Decimal("0"):
        raise HTTPException(status_code=400, detail="La cantidad producida debe ser mayor a 0.")

    if data.precio_venta <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Debes ingresar o confirmar un Precio de Venta válido (mayor a 0).")

    # VALIDACIÓN 3: BODEGA DE DESTINO OBLIGATORIA
    if not data.bodega_destino_id:
        raise HTTPException(status_code=400, detail="Es obligatorio seleccionar la Bodega de Destino para ingresar el producto terminado.")

    dest_wh_uuid = _parse_uuid(data.bodega_destino_id, "Bodega de Destino")

    # Verificar existencia de bodega destino
    stmt_dest_wh = select(Warehouse).where(
        Warehouse.id == dest_wh_uuid,
        Warehouse.is_active == True,
        Warehouse.is_deleted == False
    )
    res_dest_wh = await db.execute(stmt_dest_wh)
    dest_warehouse = res_dest_wh.scalar_one_or_none()
    if not dest_warehouse:
        raise HTTPException(status_code=404, detail="La Bodega de Destino seleccionada no existe o está inactiva.")

    # ---------------------------------------------------------
    # GESTIÓN DEL PRODUCTO FINAL: CREAR NUEVO O USAR EXISTENTE
    # ---------------------------------------------------------
    producto_final = None

    if data.producto_nuevo and data.producto_nuevo.nombre and data.producto_nuevo.nombre.strip():
        # Generar o respetar correlativo interno (SKU)
        correlativo = data.producto_nuevo.correlativo_interno
        if not correlativo or not correlativo.strip():
            correlativo = await _get_next_product_sku(db, current_user.company_id)
        else:
            correlativo = correlativo.strip()

        cat_uuid = _parse_uuid(data.producto_nuevo.categoria_id, "Categoría") if data.producto_nuevo.categoria_id else None

        # Crear el nuevo producto en el catálogo
        producto_final = Product(
            company_id=current_user.company_id,
            name=data.producto_nuevo.nombre.strip(),
            barcode=data.producto_nuevo.codigo_barras.strip() if data.producto_nuevo.codigo_barras else None,
            internal_ref=correlativo,
            category_id=cat_uuid,
            product_type=ProductType.PRODUCT,
            cost_price=Decimal("0"),
            sale_price=data.precio_venta,
            is_active=True
        )
        db.add(producto_final)
        await db.flush()

    elif data.producto_final_id:
        prod_final_uuid = _parse_uuid(data.producto_final_id, "Producto Final")
        stmt_prod_final = select(Product).where(
            Product.id == prod_final_uuid,
            Product.company_id == current_user.company_id,
            Product.is_deleted == False
        )
        res_prod_final = await db.execute(stmt_prod_final)
        producto_final = res_prod_final.scalar_one_or_none()
        if not producto_final:
            raise HTTPException(status_code=404, detail="El Producto Final seleccionado no existe.")
    else:
        raise HTTPException(
            status_code=400,
            detail="Debes especificar el nombre, código de barras, correlativo interno y categoría del producto nuevo a fabricar."
        )

    # ---------------------------------------------------------
    # INICIO DE TRANSACCIÓN ATÓMICA Y VERIFICACIÓN DE INSUMOS
    # ---------------------------------------------------------
    inv_service = InventoryService(db)

    insumos_procesados = []
    costo_total_acumulado = Decimal("0")

    for idx, item in enumerate(data.insumos, start=1):
        if item.cantidad <= Decimal("0"):
            raise HTTPException(status_code=400, detail=f"La cantidad del insumo #{idx} debe ser mayor a 0.")

        insumo_prod_uuid = _parse_uuid(item.producto_id, f"Insumo #{idx} (Producto)")
        insumo_wh_uuid = _parse_uuid(item.bodega_origen_id, f"Insumo #{idx} (Bodega Origen)")

        # Cargar producto insumo
        stmt_ins_prod = select(Product).where(
            Product.id == insumo_prod_uuid,
            Product.company_id == current_user.company_id,
            Product.is_deleted == False
        )
        res_ins_prod = await db.execute(stmt_ins_prod)
        insumo_product = res_ins_prod.scalar_one_or_none()
        if not insumo_product:
            raise HTTPException(status_code=404, detail=f"El insumo #{idx} no existe en el catálogo.")

        # Cargar bodega origen
        stmt_ins_wh = select(Warehouse).where(
            Warehouse.id == insumo_wh_uuid,
            Warehouse.is_active == True,
            Warehouse.is_deleted == False
        )
        res_ins_wh = await db.execute(stmt_ins_wh)
        insumo_warehouse = res_ins_wh.scalar_one_or_none()
        if not insumo_warehouse:
            raise HTTPException(status_code=404, detail=f"La bodega de origen del insumo '{insumo_product.name}' no existe o está inactiva.")

        # BLOQUEO DE FILA (SELECT_FOR_UPDATE en Postgres) PARA VERIFICACIÓN ATÓMICA DE STOCK
        stmt_quant = (
            select(StockQuant)
            .where(
                StockQuant.product_id == insumo_prod_uuid,
                StockQuant.warehouse_id == insumo_wh_uuid,
                StockQuant.variant_id.is_(None),
                StockQuant.is_deleted == False
            )
        )
        if "sqlite" not in settings.DATABASE_URL:
            stmt_quant = stmt_quant.with_for_update()
        res_quant = await db.execute(stmt_quant)
        quant = res_quant.scalar_one_or_none()

        stock_disponible = (quant.qty_on_hand - quant.qty_reserved) if quant else Decimal("0")

        # VALIDACIÓN DE STOCK
        if stock_disponible < item.cantidad:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Stock insuficiente para el insumo '{insumo_product.name}' en la bodega '{insumo_warehouse.name}'. "
                    f"Disponible: {float(stock_disponible)}, Requerido: {float(item.cantidad)}."
                )
            )

        unit_cost = insumo_product.cost_price or Decimal("0")
        subtotal_cost = unit_cost * item.cantidad
        costo_total_acumulado += subtotal_cost

        insumos_procesados.append({
            "product": insumo_product,
            "warehouse": insumo_warehouse,
            "cantidad": item.cantidad,
            "unit_cost": unit_cost,
            "subtotal_cost": subtotal_cost
        })

    # Calcular costo unitario del producto final
    costo_unitario_final = Decimal("0")
    if data.cantidad_producida > Decimal("0"):
        costo_unitario_final = round(costo_total_acumulado / data.cantidad_producida, 4)

    # Generar correlativo de la fabricación
    stmt_count = select(func.count(OrdenFabricacion.id)).where(OrdenFabricacion.company_id == current_user.company_id)
    res_count = await db.execute(stmt_count)
    seq = (res_count.scalar() or 0) + 1
    numero_mo = _generate_fabricacion_numero(seq)

    # ---------------------------------------------------------
    # RESTAR STOCK DE INSUMOS EN SUS BODEGAS DE ORIGEN
    # ---------------------------------------------------------
    for item_p in insumos_procesados:
        await inv_service.create_move(
            warehouse_id=item_p["warehouse"].id,
            product_id=item_p["product"].id,
            move_type=StockMoveType.ADJUSTMENT_OUT,
            qty=item_p["cantidad"],
            unit_cost=item_p["unit_cost"],
            reference=numero_mo,
            user_id=current_user.id,
            notes=f"Insumo consumido para fabricar {producto_final.name} ({numero_mo}) de {item_p['warehouse'].name}"
        )

    # ---------------------------------------------------------
    # SUMAR STOCK DEL PRODUCTO FINAL EN LA BODEGA DE DESTINO
    # ---------------------------------------------------------
    await inv_service.create_move(
        warehouse_id=dest_warehouse.id,
        product_id=producto_final.id,
        move_type=StockMoveType.ADJUSTMENT_IN,
        qty=data.cantidad_producida,
        unit_cost=costo_unitario_final,
        reference=numero_mo,
        user_id=current_user.id,
        notes=f"Producto final terminado vía fabricación {numero_mo} en {dest_warehouse.name}"
    )

    # ---------------------------------------------------------
    # ACTUALIZAR COSTO (SUMA INSUMOS) Y PRECIO DE VENTA EN PRODUCTO
    # ---------------------------------------------------------
    producto_final.cost_price = costo_unitario_final
    producto_final.sale_price = data.precio_venta

    # ---------------------------------------------------------
    # GESTIÓN DE RECETA FAVORITA SI SE MARCÓ EL CHECKBOX
    # ---------------------------------------------------------
    receta_favorita_id = None
    if data.guardar_como_favorito:
        nombre_rec = (data.nombre_receta or "").strip() or f"Fórmula de {producto_final.name}"

        stmt_rf = select(RecetaFavorita).where(
            RecetaFavorita.company_id == current_user.company_id,
            RecetaFavorita.producto_final_id == producto_final.id,
            RecetaFavorita.nombre == nombre_rec,
            RecetaFavorita.is_deleted == False
        )
        res_rf = await db.execute(stmt_rf)
        receta_fav = res_rf.scalar_one_or_none()

        if not receta_fav:
            receta_fav = RecetaFavorita(
                company_id=current_user.company_id,
                nombre=nombre_rec,
                producto_final_id=producto_final.id,
                bodega_destino_id=dest_warehouse.id,
                cantidad_producida=data.cantidad_producida,
                precio_venta_sugerido=data.precio_venta,
                costo_total_estimado=costo_total_acumulado,
                is_active=True
            )
            db.add(receta_fav)
            await db.flush()
        else:
            receta_fav.bodega_destino_id = dest_warehouse.id
            receta_fav.cantidad_producida = data.cantidad_producida
            receta_fav.precio_venta_sugerido = data.precio_venta
            receta_fav.costo_total_estimado = costo_total_acumulado
            stmt_del_det = select(DetalleReceta).where(DetalleReceta.receta_id == receta_fav.id)
            res_del_det = await db.execute(stmt_del_det)
            for d in res_del_det.scalars().all():
                await db.delete(d)
            await db.flush()

        for item_p in insumos_procesados:
            det_rec = DetalleReceta(
                receta_id=receta_fav.id,
                producto_id=item_p["product"].id,
                bodega_origen_id=item_p["warehouse"].id,
                cantidad=item_p["cantidad"]
            )
            db.add(det_rec)

        receta_favorita_id = receta_fav.id

    # ---------------------------------------------------------
    # REGISTRAR ORDEN DE FABRICACIÓN HISTÓRICA
    # ---------------------------------------------------------
    orden = OrdenFabricacion(
        company_id=current_user.company_id,
        numero=numero_mo,
        producto_final_id=producto_final.id,
        bodega_destino_id=dest_warehouse.id,
        cantidad_producida=data.cantidad_producida,
        costo_total=costo_total_acumulado,
        costo_unitario=costo_unitario_final,
        precio_venta=data.precio_venta,
        estado="done",
        receta_id=receta_favorita_id,
        user_id=current_user.id,
        fecha=datetime.now(timezone.utc),
        notas=data.notas
    )
    db.add(orden)
    await db.flush()

    detalles_out = []
    for item_p in insumos_procesados:
        det_fab = DetalleFabricacion(
            orden_id=orden.id,
            producto_id=item_p["product"].id,
            bodega_origen_id=item_p["warehouse"].id,
            cantidad=item_p["cantidad"],
            costo_unitario=item_p["unit_cost"],
            costo_subtotal=item_p["subtotal_cost"]
        )
        db.add(det_fab)
        await db.flush()

        detalles_out.append(InsumoDetalleOut(
            id=str(det_fab.id),
            producto_id=str(item_p["product"].id),
            producto_nombre=item_p["product"].name,
            producto_ref=item_p["product"].internal_ref or "",
            bodega_origen_id=str(item_p["warehouse"].id),
            bodega_origen_nombre=item_p["warehouse"].name,
            cantidad=float(item_p["cantidad"]),
            costo_unitario=float(item_p["unit_cost"]),
            costo_subtotal=float(item_p["subtotal_cost"])
        ))

    # COMMIT ATÓMICO FINAL
    await db.commit()

    margen_pct = 0.0
    if float(data.precio_venta) > 0 and float(costo_unitario_final) > 0:
        margen_pct = round(((float(data.precio_venta) - float(costo_unitario_final)) / float(data.precio_venta)) * 100, 2)

    return OrdenFabricacionOut(
        id=str(orden.id),
        numero=orden.numero,
        producto_final_id=str(producto_final.id),
        producto_final_nombre=producto_final.name,
        producto_final_ref=producto_final.internal_ref or "",
        producto_final_barcode=producto_final.barcode or "",
        bodega_destino_id=str(dest_warehouse.id),
        bodega_destino_nombre=dest_warehouse.name,
        cantidad_producida=float(orden.cantidad_producida),
        costo_total=float(orden.costo_total),
        costo_unitario=float(orden.costo_unitario),
        precio_venta=float(orden.precio_venta),
        margen_estimado_pct=margen_pct,
        estado=orden.estado,
        fecha=orden.fecha.strftime("%d/%m/%Y %H:%M") if orden.fecha else "Hoy",
        notas=orden.notas,
        insumos=detalles_out
    )


# ============================================================================
# 4. HISTORIAL DE FABRICACIONES EJECUTADAS
# ============================================================================

@router.get("/historial", response_model=List[OrdenFabricacionOut])
async def list_historial_fabricacion(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar historial de todas las órdenes de fabricación ejecutadas con sus insumos y bodegas."""
    stmt = (
        select(OrdenFabricacion)
        .options(
            selectinload(OrdenFabricacion.producto_final),
            selectinload(OrdenFabricacion.bodega_destino),
            selectinload(OrdenFabricacion.detalles).selectinload(DetalleFabricacion.producto),
            selectinload(OrdenFabricacion.detalles).selectinload(DetalleFabricacion.bodega_origen),
        )
        .where(
            OrdenFabricacion.company_id == current_user.company_id,
            OrdenFabricacion.is_deleted == False
        )
        .order_by(OrdenFabricacion.created_at.desc())
    )
    res = await db.execute(stmt)
    ordenes = res.scalars().all()

    output = []
    for o in ordenes:
        detalles_out = []
        for d in o.detalles:
            detalles_out.append(InsumoDetalleOut(
                id=str(d.id),
                producto_id=str(d.producto_id),
                producto_nombre=d.producto.name if d.producto else "Insumo",
                producto_ref=d.producto.internal_ref if d.producto else "",
                bodega_origen_id=str(d.bodega_origen_id),
                bodega_origen_nombre=d.bodega_origen.name if d.bodega_origen else "Bodega",
                cantidad=float(d.cantidad),
                costo_unitario=float(d.costo_unitario or 0),
                costo_subtotal=float(d.costo_subtotal or 0)
            ))

        margen_pct = 0.0
        pv = float(o.precio_venta or 0)
        cu = float(o.costo_unitario or 0)
        if pv > 0 and cu > 0:
            margen_pct = round(((pv - cu) / pv) * 100, 2)

        output.append(OrdenFabricacionOut(
            id=str(o.id),
            numero=o.numero,
            producto_final_id=str(o.producto_final_id),
            producto_final_nombre=o.producto_final.name if o.producto_final else "Producto Desconocido",
            producto_final_ref=o.producto_final.internal_ref if o.producto_final else "",
            producto_final_barcode=o.producto_final.barcode if o.producto_final else "",
            bodega_destino_id=str(o.bodega_destino_id),
            bodega_destino_nombre=o.bodega_destino.name if o.bodega_destino else "Bodega Destino",
            cantidad_producida=float(o.cantidad_producida),
            costo_total=float(o.costo_total),
            costo_unitario=float(o.costo_unitario),
            precio_venta=float(o.precio_venta),
            margen_estimado_pct=margen_pct,
            estado=o.estado,
            fecha=o.fecha.strftime("%d/%m/%Y %H:%M") if o.fecha else "Hoy",
            notas=o.notas,
            insumos=detalles_out
        ))

    return output


# ============================================================================
# 5. RETROCOMPATIBILIDAD CON VISTAS PREVIAS
# ============================================================================

@router.get("/orders")
async def list_orders_compat(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Alias retrocompatible."""
    return await list_historial_fabricacion(db, current_user)


@router.get("/boms")
async def list_boms_compat(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Alias retrocompatible."""
    return await list_recetas_favoritas(db, current_user)


@router.get("/products")
async def list_products_compat(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Alias retrocompatible."""
    return await list_products_stock_mfg(db, current_user)
