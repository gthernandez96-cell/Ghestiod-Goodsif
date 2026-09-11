"""API v1 — Productos y Categorías."""
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
import uuid
import os

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.product import Product, ProductCategory, ProductType, TrackingMethod, UnitOfMeasure
from app.models.inventory import StockQuant, StockMove, StockMoveType
from app.models.company import Warehouse, Branch

router = APIRouter(prefix="/products")


def check_product_management_permission(current_user):
    """Verifica que el usuario no sea solo un cajero sin permisos de gestión de catálogo."""
    if getattr(current_user, "is_superuser", False):
        return
    role_codes = [r.code for r in current_user.roles] if hasattr(current_user, "roles") and current_user.roles else []
    if any(code in role_codes for code in ["admin", "inventory_manager", "store_manager"]):
        return
    if "cashier" in role_codes or not role_codes:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: El usuario con rol Cajero no tiene autorización para agregar o modificar productos ni alterar precios de venta."
        )



@router.post("/upload-image")
async def upload_product_image(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Subir imagen de producto desde el ordenador."""
    filename = file.filename or "image.png"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa JPG, PNG, WEBP o GIF.")

    app_dir = os.path.dirname(os.path.abspath(__file__))
    upload_dir = os.path.abspath(os.path.join(app_dir, "../../../../frontend/static/uploads/products"))
    os.makedirs(upload_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(upload_dir, unique_name)

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen no debe exceder los 5MB.")

    with open(file_path, "wb") as f:
        f.write(content)

    url_path = f"/static/uploads/products/{unique_name}"
    return {
        "url": url_path,
        "filename": unique_name,
        "status": "uploaded"
    }


def _val_to_str(val) -> str:
    if val is None:
        return ""
    if hasattr(val, "value"):
        return str(val.value)
    return str(val)


# ===== SCHEMAS =====
class CategoryCreateIn(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None
    tax_rate: Decimal = Decimal("12.00")


class UOMCreateIn(BaseModel):
    name: str
    symbol: str
    category: str = "unit"
    ratio: Decimal = Decimal("1.0")
    is_base: bool = False


class ProductCreateIn(BaseModel):
    name: str
    internal_ref: Optional[str] = None
    barcode: Optional[str] = None
    category_id: Optional[str] = None
    supplier_id: Optional[str] = None
    uom_id: Optional[str] = None
    wholesale_price: Optional[Decimal] = Decimal("0")
    wholesale_uom_id: Optional[str] = None
    wholesale_ratio: Optional[Decimal] = Decimal("1.0")
    description: Optional[str] = None
    product_type: str = "product"
    tracking: str = "none"
    sale_price: Decimal = Decimal("0")
    cost_price: Decimal = Decimal("0")
    min_price: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("12.00")
    includes_tax: bool = True
    min_stock: Decimal = Decimal("0")
    max_stock: Decimal = Decimal("0")
    is_active: bool = True
    can_be_sold: bool = True
    can_be_purchased: bool = True
    image_url: Optional[str] = None


class ProductUpdateIn(BaseModel):
    name: Optional[str] = None
    internal_ref: Optional[str] = None
    barcode: Optional[str] = None
    category_id: Optional[str] = None
    supplier_id: Optional[str] = None
    uom_id: Optional[str] = None
    wholesale_price: Optional[Decimal] = None
    wholesale_uom_id: Optional[str] = None
    wholesale_ratio: Optional[Decimal] = None
    description: Optional[str] = None
    sale_price: Optional[Decimal] = None
    cost_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    min_stock: Optional[Decimal] = None
    max_stock: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    is_active: Optional[bool] = None
    image_url: Optional[str] = None


class StockAdjustIn(BaseModel):
    new_qty: Decimal
    warehouse_id: Optional[str] = None
    reason: str = "conteo_fisico"
    notes: Optional[str] = None


# ===== CATEGORIES ENDPOINTS =====
@router.get("/categories")
async def list_categories(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar todas las categorías de productos."""
    stmt = (
        select(ProductCategory)
        .where(ProductCategory.company_id == current_user.company_id)
        .order_by(ProductCategory.name)
    )
    result = await db.execute(stmt)
    categories = result.scalars().all()

    return [
        {
            "id": str(c.id),
            "name": c.name,
            "code": c.code,
            "description": c.description,
            "parent_id": str(c.parent_id) if c.parent_id else None,
            "tax_rate": float(c.tax_rate) if c.tax_rate else 12.0,
            "is_active": c.is_active,
        }
        for c in categories
    ]


@router.post("/categories")
async def create_category(
    data: CategoryCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva categoría."""
    check_product_management_permission(current_user)
    category = ProductCategory(
        company_id=current_user.company_id,
        name=data.name,
        code=data.code,
        description=data.description,
        parent_id=uuid.UUID(data.parent_id) if data.parent_id else None,
        tax_rate=data.tax_rate,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return {"id": str(category.id), "name": category.name, "status": "created"}


# ===== UNITS OF MEASURE (UOM) ENDPOINTS =====
@router.get("/uoms")
async def list_uoms(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar todas las unidades de medida activas de la empresa."""
    stmt = (
        select(UnitOfMeasure)
        .where(
            UnitOfMeasure.company_id == current_user.company_id,
            UnitOfMeasure.is_active == True,
            UnitOfMeasure.is_deleted == False
        )
        .order_by(UnitOfMeasure.name)
    )
    result = await db.execute(stmt)
    uoms = result.scalars().all()

    return [
        {
            "id": str(u.id),
            "name": u.name,
            "symbol": u.symbol,
            "category": u.category,
            "ratio": float(u.ratio) if u.ratio else 1.0,
            "is_base": bool(u.is_base),
            "is_active": bool(u.is_active),
        }
        for u in uoms
    ]


@router.post("/uoms")
async def create_uom(
    data: UOMCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva unidad de medida."""
    check_product_management_permission(current_user)
    uom = UnitOfMeasure(
        company_id=current_user.company_id,
        name=data.name.strip(),
        symbol=data.symbol.strip(),
        category=data.category,
        ratio=data.ratio,
        is_base=data.is_base,
        is_active=True,
    )
    db.add(uom)
    await db.commit()
    await db.refresh(uom)
    return {
        "id": str(uom.id),
        "name": uom.name,
        "symbol": uom.symbol,
        "status": "created"
    }


import re


async def get_next_product_sku(db: AsyncSession, company_id: uuid.UUID) -> str:
    """Genera el siguiente número correlativo de Referencia Interna (SKU) para la empresa."""
    stmt = select(Product.internal_ref).where(Product.company_id == company_id)
    res = await db.execute(stmt)
    existing_refs = [r for r in res.scalars().all() if r]

    max_num = 0
    for ref in existing_refs:
        # Extract digits from SKU e.g. PROD-0005, PRD-12, SKU-0010, ACE001 -> 5, 12, 10, 1
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


# ===== PRODUCTS ENDPOINTS =====
@router.get("/next-sku")
async def get_next_sku(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener el siguiente número correlativo disponible para SKU / Referencia Interna."""
    sku = await get_next_product_sku(db, current_user.company_id)
    return {"next_sku": sku}


@router.get("/")
async def list_products(
    search: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    supplier_id: Optional[str] = Query(None),
    warehouse_id: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar productos con filtrado y stock global/por bodega."""
    stmt = select(Product).options(
        selectinload(Product.category),
        selectinload(Product.uom),
        selectinload(Product.wholesale_uom)
    ).where(
        Product.company_id == current_user.company_id,
        Product.is_deleted == False
    )

    if isinstance(is_active, bool):
        stmt = stmt.where(Product.is_active == is_active)

    if category_id and isinstance(category_id, str) and category_id.strip():
        try:
            stmt = stmt.where(Product.category_id == uuid.UUID(category_id))
        except (ValueError, AttributeError):
            pass

    if supplier_id and isinstance(supplier_id, str) and supplier_id.strip():
        try:
            stmt = stmt.where(Product.supplier_id == uuid.UUID(supplier_id))
        except (ValueError, AttributeError):
            pass

    if search and isinstance(search, str) and search.strip():
        search_pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(search_pattern),
                Product.internal_ref.ilike(search_pattern),
                Product.barcode.ilike(search_pattern),
            )
        )

    stmt = stmt.order_by(Product.name)
    result = await db.execute(stmt)
    products = result.scalars().all()

    # Obtener stock global por producto (solo bodegas activas de la empresa)
    product_ids = [p.id for p in products]
    stock_map = {}
    if product_ids:
        quant_stmt = (
            select(
                StockQuant.product_id,
                func.sum(StockQuant.qty_on_hand).label("total_stock")
            )
            .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
            .join(Branch, Warehouse.branch_id == Branch.id)
            .where(
                StockQuant.product_id.in_(product_ids),
                StockQuant.is_deleted == False,
                Warehouse.is_active == True,
                Warehouse.is_deleted == False,
                Branch.company_id == current_user.company_id,
                Branch.is_deleted == False,
            )
        )

        if warehouse_id and isinstance(warehouse_id, str) and warehouse_id.strip():
            try:
                quant_stmt = quant_stmt.where(StockQuant.warehouse_id == uuid.UUID(warehouse_id.strip()))
            except (ValueError, AttributeError):
                pass

        quant_stmt = quant_stmt.group_by(StockQuant.product_id)
        quant_res = await db.execute(quant_stmt)
        for p_id, total in quant_res.all():
            stock_map[str(p_id)] = float(total or 0)

    items = []
    for p in products:
        p_id_str = str(p.id)
        items.append({
            "id": p_id_str,
            "name": p.name,
            "internal_ref": p.internal_ref or "",
            "barcode": p.barcode or "",
            "description": p.description or "",
            "category_id": str(p.category_id) if p.category_id else None,
            "category_name": p.category.name if p.category else "General",
            "supplier_id": getattr(p, "supplier_id", None),
            "supplier_name": None,
            "sale_price": float(p.sale_price or 0),
            "cost_price": float(p.cost_price or 0),
            "min_price": float(p.min_price or 0),
            "wholesale_price": float(p.wholesale_price or 0) if p.wholesale_price else None,
            "wholesale_uom_id": str(p.wholesale_uom_id) if p.wholesale_uom_id else None,
            "wholesale_uom_name": p.wholesale_uom.name if p.wholesale_uom else None,
            "wholesale_uom_symbol": p.wholesale_uom.symbol if p.wholesale_uom else None,
            "wholesale_ratio": float(p.wholesale_ratio or 1.0),
            "uom_id": str(p.uom_id) if p.uom_id else None,
            "uom_name": p.uom.name if p.uom else None,
            "uom_symbol": p.uom.symbol if p.uom else None,
            "tax_rate": float(p.tax_rate or 12.0),
            "includes_tax": p.includes_tax,
            "min_stock": float(p.min_stock or 0),
            "stock": stock_map.get(p_id_str, 0.0),
            "product_type": _val_to_str(p.product_type),
            "tracking": _val_to_str(p.tracking),
            "is_active": p.is_active,
            "can_be_sold": p.can_be_sold,
            "image_url": p.image_url,
        })

    return {"items": items, "total": len(items)}


@router.get("/pos-catalog")
async def get_pos_catalog(
    warehouse_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Descargar catálogo completo y consolidado para sincronización rápida en IndexedDB."""
    # 1. Categorías
    cat_stmt = select(ProductCategory).where(
        ProductCategory.company_id == current_user.company_id,
        ProductCategory.is_active == True
    ).order_by(ProductCategory.name)
    cat_res = await db.execute(cat_stmt)
    categories = [
        {
            "id": str(c.id),
            "name": c.name,
            "code": c.code,
            "tax_rate": float(c.tax_rate) if c.tax_rate else 12.0,
        }
        for c in cat_res.scalars().all()
    ]

    # 2. Productos activos para venta
    prod_stmt = select(Product).options(
        selectinload(Product.category),
        selectinload(Product.uom),
        selectinload(Product.wholesale_uom)
    ).where(
        Product.company_id == current_user.company_id,
        Product.is_active == True,
        Product.is_deleted == False,
        Product.can_be_sold == True
    ).order_by(Product.name)
    prod_res = await db.execute(prod_stmt)
    products = prod_res.scalars().all()

    # 3. Stock
    product_ids = [p.id for p in products]
    stock_map = {}
    if product_ids:
        quant_stmt = (
            select(
                StockQuant.product_id,
                func.sum(StockQuant.qty_on_hand).label("total_stock")
            )
            .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
            .join(Branch, Warehouse.branch_id == Branch.id)
            .where(
                StockQuant.product_id.in_(product_ids),
                StockQuant.is_deleted == False,
                Warehouse.is_active == True,
                Warehouse.is_deleted == False,
                Branch.company_id == current_user.company_id,
                Branch.is_deleted == False,
            )
        )

        if warehouse_id and isinstance(warehouse_id, str) and warehouse_id.strip():
            try:
                quant_stmt = quant_stmt.where(StockQuant.warehouse_id == uuid.UUID(warehouse_id.strip()))
            except (ValueError, AttributeError):
                pass

        quant_stmt = quant_stmt.group_by(StockQuant.product_id)
        quant_res = await db.execute(quant_stmt)
        for p_id, total in quant_res.all():
            stock_map[str(p_id)] = float(total or 0)

    items = []
    for p in products:
        p_id_str = str(p.id)
        w_price = float(p.wholesale_price or (p.attributes or {}).get("wholesale_price", 0)) if (p.wholesale_price or (p.attributes or {}).get("wholesale_price")) else None
        items.append({
            "id": p_id_str,
            "name": p.name,
            "internal_ref": p.internal_ref,
            "barcode": p.barcode,
            "description": p.description,
            "category_id": str(p.category_id) if p.category_id else None,
            "category_name": p.category.name if p.category else None,
            "sale_price": float(p.sale_price or 0),
            "cost_price": float(p.cost_price or 0),
            "min_price": float(p.min_price or 0),
            "wholesale_price": w_price,
            "wholesale_uom_id": str(p.wholesale_uom_id) if p.wholesale_uom_id else None,
            "wholesale_uom_name": p.wholesale_uom.name if p.wholesale_uom else None,
            "wholesale_uom_symbol": p.wholesale_uom.symbol if p.wholesale_uom else None,
            "wholesale_ratio": float(p.wholesale_ratio or 1.0),
            "uom_id": str(p.uom_id) if p.uom_id else None,
            "uom_name": p.uom.name if p.uom else None,
            "uom_symbol": p.uom.symbol if p.uom else None,
            "tax_rate": float(p.tax_rate or 12.0),
            "includes_tax": p.includes_tax,
            "is_active": p.is_active,
            "stock": stock_map.get(p_id_str, 0.0),
            "image_url": p.image_url,
            "product_type": _val_to_str(p.product_type),
            "attributes": p.attributes or {},
            "wholesale_min_qty": float((p.attributes or {}).get("wholesale_min_qty", 0)) if (p.attributes or {}).get("wholesale_min_qty") else None,
            "is_combo": bool((p.attributes or {}).get("is_combo", False)),
            "combo_items": (p.attributes or {}).get("combo_items", []),
        })

    from datetime import datetime, timezone
    return {
        "products": items,
        "categories": categories,
        "total_products": len(items),
        "total_categories": len(categories),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/kpis")
async def get_inventory_kpis(
    warehouse_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener métricas y KPIs ejecutivos de inventario y valoración de bodega."""
    stmt = select(Product).where(Product.company_id == current_user.company_id, Product.is_active == True, Product.is_deleted == False)
    res = await db.execute(stmt)
    products = res.scalars().all()

    product_ids = [p.id for p in products]
    stock_map = {}
    if product_ids:
        quant_stmt = (
            select(
                StockQuant.product_id,
                func.sum(StockQuant.qty_on_hand).label("total_stock")
            )
            .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
            .join(Branch, Warehouse.branch_id == Branch.id)
            .where(
                StockQuant.product_id.in_(product_ids),
                StockQuant.is_deleted == False,
                Warehouse.is_active == True,
                Warehouse.is_deleted == False,
                Branch.company_id == current_user.company_id,
                Branch.is_deleted == False,
            )
        )

        if warehouse_id and isinstance(warehouse_id, str) and warehouse_id.strip():
            try:
                quant_stmt = quant_stmt.where(StockQuant.warehouse_id == uuid.UUID(warehouse_id.strip()))
            except (ValueError, AttributeError):
                pass

        quant_stmt = quant_stmt.group_by(StockQuant.product_id)
        quant_res = await db.execute(quant_stmt)
        for p_id, total in quant_res.all():
            stock_map[p_id] = float(total or 0)

    total_products = len(products)
    total_valuation = Decimal("0")
    low_stock_count = 0
    out_of_stock_count = 0
    in_stock_count = 0

    for p in products:
        stk = stock_map.get(p.id, 0.0)
        c_cost = Decimal(str(p.cost_price or 0))
        total_valuation += Decimal(str(stk)) * c_cost

        if stk <= 0:
            out_of_stock_count += 1
        elif stk <= float(p.min_stock or 5.0):
            low_stock_count += 1
            in_stock_count += 1
        else:
            in_stock_count += 1

    return {
        "total_products": total_products,
        "total_valuation": float(total_valuation),
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "in_stock_count": in_stock_count,
    }


@router.get("/stock-levels")
@router.get("/stock-sync")
async def get_products_stock_levels(
    warehouse_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retorna mapeo ligero {product_id: stock} para sincronización automática en tiempo real del POS."""
    quant_stmt = (
        select(
            StockQuant.product_id,
            func.sum(StockQuant.qty_on_hand).label("total_stock")
        )
        .join(Product, Product.id == StockQuant.product_id)
        .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
        .join(Branch, Warehouse.branch_id == Branch.id)
        .where(
            Product.company_id == current_user.company_id,
            Product.is_deleted == False,
            Product.is_active == True,
            Product.can_be_sold == True,
            StockQuant.is_deleted == False,
            Warehouse.is_active == True,
            Warehouse.is_deleted == False,
            Branch.company_id == current_user.company_id,
            Branch.is_deleted == False,
        )
    )
    if warehouse_id and isinstance(warehouse_id, str) and warehouse_id.strip():
        try:
            quant_stmt = quant_stmt.where(StockQuant.warehouse_id == uuid.UUID(warehouse_id.strip()))
        except (ValueError, AttributeError):
            pass

    quant_stmt = quant_stmt.group_by(StockQuant.product_id)
    res = await db.execute(quant_stmt)
    stock_map = {str(row[0]): float(row[1] or 0) for row in res.all()}
    return {"stock": stock_map, "count": len(stock_map)}


# ===== IMPORTACIÓN / EXPORTACIÓN CSV =====
@router.get("/template-csv")
async def download_product_csv_template():
    """Descargar plantilla CSV para importación masiva de productos."""
    import csv
    import io
    from fastapi.responses import Response

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "codigo_interno", "codigo_barras", "nombre", "categoria",
        "precio_venta", "precio_costo", "stock_minimo", "stock_inicial",
        "unidad_medida", "descripcion"
    ])
    writer.writerow([
        "PROD-001", "740100100101", "Coca Cola 2.5L Retornable", "Bebidas",
        "15.00", "11.50", "10", "50",
        "Unidad", "Bebida gaseosa familiar"
    ])
    writer.writerow([
        "PROD-002", "740100100102", "Arroz Blanco Superior 1lb", "Abarrotes",
        "6.50", "4.80", "20", "100",
        "Libra", "Arroz grano entero"
    ])

    csv_data = "\ufeff" + output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="plantilla_productos.csv"'}
    )


@router.post("/import-csv")
async def import_products_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Importar productos masivamente desde un archivo CSV."""
    check_product_management_permission(current_user)
    import csv
    import io

    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")
    csv_reader = csv.DictReader(io.StringIO(text))

    if not csv_reader.fieldnames:
        raise HTTPException(status_code=400, detail="El archivo CSV está vacío o mal formateado.")

    def clean_col(s):
        return s.strip().lower().replace(" ", "_").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")

    created_count = 0
    updated_count = 0
    errors = []

    wh_stmt = select(Warehouse).join(Branch, Warehouse.branch_id == Branch.id).where(
        Branch.company_id == current_user.company_id,
        Warehouse.is_active == True
    )
    wh_res = await db.execute(wh_stmt)
    default_wh = wh_res.scalars().first()

    for idx, raw_row in enumerate(csv_reader, start=2):
        row = {clean_col(k): v.strip() for k, v in raw_row.items() if k}
        name = row.get("nombre") or row.get("name") or row.get("descripcion_corta")
        if not name:
            errors.append(f"Fila {idx}: Falta el nombre del producto.")
            continue

        internal_ref = row.get("codigo_interno") or row.get("sku") or row.get("codigo") or ""
        barcode = row.get("codigo_barras") or row.get("barcode") or row.get("codigo_barra") or ""
        category_name = row.get("categoria") or row.get("category") or "General"
        
        try:
            sale_price = Decimal(str(row.get("precio_venta") or row.get("precio") or "0").replace(",", "."))
        except Exception:
            sale_price = Decimal("0")

        try:
            cost_price = Decimal(str(row.get("precio_costo") or row.get("costo") or "0").replace(",", "."))
        except Exception:
            cost_price = Decimal("0")

        try:
            min_stock = Decimal(str(row.get("stock_minimo") or "0").replace(",", "."))
        except Exception:
            min_stock = Decimal("0")

        try:
            init_stock = Decimal(str(row.get("stock_inicial") or row.get("existencia") or "0").replace(",", "."))
        except Exception:
            init_stock = Decimal("0")

        description = row.get("descripcion") or ""

        cat_stmt = select(ProductCategory).where(
            ProductCategory.company_id == current_user.company_id,
            func.lower(ProductCategory.name) == category_name.lower()
        )
        cat_res = await db.execute(cat_stmt)
        cat = cat_res.scalars().first()
        if not cat:
            cat = ProductCategory(
                company_id=current_user.company_id,
                name=category_name,
                code=category_name[:10].upper()
            )
            db.add(cat)
            await db.flush()

        existing_prod = None
        if internal_ref or barcode:
            filters = []
            if internal_ref:
                filters.append(Product.internal_ref == internal_ref)
            if barcode:
                filters.append(Product.barcode == barcode)
            prod_stmt = select(Product).where(
                Product.company_id == current_user.company_id,
                or_(*filters)
            )
            prod_res = await db.execute(prod_stmt)
            existing_prod = prod_res.scalars().first()

        if existing_prod:
            existing_prod.name = name
            existing_prod.sale_price = sale_price
            if cost_price > 0:
                existing_prod.cost_price = cost_price
            existing_prod.category_id = cat.id
            if min_stock > 0:
                existing_prod.min_stock = min_stock
            if description:
                existing_prod.description = description
            updated_count += 1
            prod_obj = existing_prod
        else:
            prod_obj = Product(
                company_id=current_user.company_id,
                category_id=cat.id,
                name=name,
                internal_ref=internal_ref or f"PRD-{uuid.uuid4().hex[:6].upper()}",
                barcode=barcode or None,
                sale_price=sale_price,
                cost_price=cost_price,
                min_stock=min_stock,
                description=description,
                is_active=True,
                is_published=True,
                can_be_sold=True,
                can_be_purchased=True
            )
            db.add(prod_obj)
            await db.flush()
            created_count += 1

        if init_stock > 0 and default_wh:
            quant_stmt = select(StockQuant).where(
                StockQuant.product_id == prod_obj.id,
                StockQuant.warehouse_id == default_wh.id
            )
            quant_res = await db.execute(quant_stmt)
            quant = quant_res.scalar_one_or_none()
            if not quant:
                quant = StockQuant(
                    warehouse_id=default_wh.id,
                    product_id=prod_obj.id,
                    qty_on_hand=init_stock,
                    qty_reserved=Decimal("0")
                )
                db.add(quant)
            else:
                quant.qty_on_hand += init_stock

    await db.commit()
    return {
        "status": "success",
        "created": created_count,
        "updated": updated_count,
        "errors": errors,
        "total_processed": created_count + updated_count
    }


@router.get("/export-csv")
async def export_products_csv(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar catálogo completo de productos en formato CSV compatible con Excel."""
    import csv
    import io
    from fastapi.responses import Response

    stmt = select(Product).options(
        selectinload(Product.category)
    ).where(
        Product.company_id == current_user.company_id,
        Product.is_active == True
    ).order_by(Product.name)

    res = await db.execute(stmt)
    products = res.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "ID", "Código Interno", "Código de Barras", "Nombre",
        "Categoría", "Precio Venta (Q)", "Precio Costo (Q)", "Stock Mínimo", "Estado"
    ])

    for p in products:
        cat_name = p.category.name if p.category else "Sin Categoría"
        writer.writerow([
            str(p.id),
            p.internal_ref or "",
            p.barcode or "",
            p.name,
            cat_name,
            f"{float(p.sale_price or 0):.2f}",
            f"{float(p.cost_price or 0):.2f}",
            f"{float(p.min_stock or 0):.2f}",
            "Activo" if p.is_active else "Inactivo"
        ])

    csv_data = "\ufeff" + output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="catalogo_productos.csv"'}
    )


@router.get("/{product_id}")
async def get_product(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle de un producto."""
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    stmt = select(Product).options(
        selectinload(Product.category),
        selectinload(Product.supplier),
        selectinload(Product.uom),
        selectinload(Product.wholesale_uom)
    ).where(
        Product.id == p_uuid, Product.company_id == current_user.company_id
    )
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    # Stock por bodega estructurado
    wh_stmt = (
        select(Warehouse)
        .join(Branch, Warehouse.branch_id == Branch.id)
        .options(selectinload(Warehouse.branch))
        .where(
            Branch.company_id == current_user.company_id,
            Warehouse.is_active == True,
            Warehouse.is_deleted == False
        )
        .order_by(Warehouse.name)
    )
    wh_res = await db.execute(wh_stmt)
    all_warehouses = wh_res.scalars().all()

    quant_stmt = select(StockQuant).where(StockQuant.product_id == p_uuid)
    quant_res = await db.execute(quant_stmt)
    quants = quant_res.scalars().all()
    quant_map = {str(q.warehouse_id): float(q.qty_on_hand or 0) for q in quants}

    warehouse_list = []
    total_stock = 0.0
    for wh in all_warehouses:
        qty = quant_map.get(str(wh.id), 0.0)
        total_stock += qty
        warehouse_list.append({
            "warehouse_id": str(wh.id),
            "warehouse_name": wh.name,
            "warehouse_code": wh.code or "",
            "branch_id": str(wh.branch_id) if wh.branch_id else None,
            "branch_name": wh.branch.name if wh.branch else "Principal",
            "qty_on_hand": qty,
            "is_main": getattr(wh, "is_default", False),
        })

    # Balance acumulado real según Kardex
    INFLOW_STRINGS = {"purchase", "transfer_in", "adj_in", "initial", "ret_sale"}
    k_res = await db.execute(select(StockMove.move_type, StockMove.qty).where(StockMove.product_id == p_uuid))
    k_moves = k_res.all()
    kardex_stock = 0.0
    for m_type_obj, m_qty in k_moves:
        m_type_str = m_type_obj.value if hasattr(m_type_obj, "value") else str(m_type_obj)
        if m_type_str in INFLOW_STRINGS:
            kardex_stock += float(m_qty or 0)
        else:
            kardex_stock -= float(m_qty or 0)

    has_kardex_discrepancy = (len(k_moves) > 0) and (abs(total_stock - kardex_stock) > 0.0001)

    return {
        "id": str(p.id),
        "name": p.name,
        "internal_ref": p.internal_ref or "",
        "barcode": p.barcode or "",
        "description": p.description or "",
        "category_id": str(p.category_id) if p.category_id else None,
        "category_name": p.category.name if p.category else None,
        "supplier_id": str(p.supplier_id) if p.supplier_id else None,
        "supplier_name": p.supplier.name if p.supplier else None,
        "uom_id": str(p.uom_id) if p.uom_id else None,
        "uom_name": p.uom.name if p.uom else None,
        "uom_symbol": p.uom.symbol if p.uom else None,
        "wholesale_price": float(p.wholesale_price or 0) if p.wholesale_price else None,
        "wholesale_uom_id": str(p.wholesale_uom_id) if p.wholesale_uom_id else None,
        "wholesale_uom_name": p.wholesale_uom.name if p.wholesale_uom else None,
        "wholesale_uom_symbol": p.wholesale_uom.symbol if p.wholesale_uom else None,
        "wholesale_ratio": float(p.wholesale_ratio or 1.0),
        "sale_price": float(p.sale_price or 0),
        "cost_price": float(p.cost_price or 0),
        "min_price": float(p.min_price or 0),
        "min_stock": float(p.min_stock or 0),
        "max_stock": float(p.max_stock or 0),
        "tax_rate": float(p.tax_rate or 12.0),
        "includes_tax": p.includes_tax,
        "is_active": p.is_active,
        "stock_by_warehouse": quant_map,
        "warehouses": warehouse_list,
        "total_stock": total_stock,
        "kardex_stock": kardex_stock if len(k_moves) > 0 else total_stock,
        "has_kardex_discrepancy": has_kardex_discrepancy,
        "image_url": p.image_url,
    }


@router.get("/{product_id}/warehouses-stock")
async def get_product_warehouses_stock(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener desglose de existencias del producto en todas las bodegas."""
    try:
        p_uuid = uuid.UUID(product_id)
        prod_stmt = select(Product).where(Product.id == p_uuid, Product.company_id == current_user.company_id)
    except ValueError:
        prod_stmt = select(Product).where(
            (Product.internal_ref == product_id) | (Product.barcode == product_id),
            Product.company_id == current_user.company_id
        )

    prod_res = await db.execute(prod_stmt)
    prod = prod_res.scalar_one_or_none()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    # 2. Obtener todas las bodegas de la empresa (uniendo a través de Branch)
    wh_stmt = (
        select(Warehouse)
        .join(Branch, Warehouse.branch_id == Branch.id)
        .options(selectinload(Warehouse.branch))
        .where(
            Branch.company_id == current_user.company_id,
            Warehouse.is_active == True,
            Warehouse.is_deleted == False
        )
        .order_by(Warehouse.name)
    )
    wh_res = await db.execute(wh_stmt)
    warehouses = wh_res.scalars().all()

    # 3. Obtener quants de stock
    quant_stmt = select(StockQuant).where(StockQuant.product_id == prod.id)
    quant_res = await db.execute(quant_stmt)
    quants = quant_res.scalars().all()
    quant_map = {str(q.warehouse_id): float(q.qty_on_hand or 0) for q in quants}

    warehouse_list = []
    total_stock = 0.0
    for wh in warehouses:
        qty = quant_map.get(str(wh.id), 0.0)
        total_stock += qty
        warehouse_list.append({
            "warehouse_id": str(wh.id),
            "warehouse_name": wh.name,
            "warehouse_code": wh.code or "",
            "branch_id": str(wh.branch_id) if wh.branch_id else None,
            "branch_name": wh.branch.name if wh.branch else "Principal",
            "qty_on_hand": qty,
            "is_main": getattr(wh, "is_main", False),
        })

    return {
        "product_id": str(prod.id),
        "product_name": prod.name,
        "internal_ref": prod.internal_ref or "",
        "barcode": prod.barcode or "",
        "sale_price": float(prod.sale_price or 0),
        "min_stock": float(prod.min_stock or 5),
        "total_stock": total_stock,
        "warehouses": warehouse_list,
    }


@router.get("/{product_id}/kardex")
async def get_product_kardex(
    product_id: str,
    warehouse_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener el Kardex / movimientos de stock de un producto con desglose de bodega y saldo acumulado."""
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    stmt = (
        select(StockMove)
        .options(selectinload(StockMove.warehouse))
        .where(StockMove.product_id == p_uuid)
    )
    if warehouse_id and isinstance(warehouse_id, str) and warehouse_id.strip():
        try:
            stmt = stmt.where(StockMove.warehouse_id == uuid.UUID(warehouse_id.strip()))
        except ValueError:
            pass

    stmt = stmt.order_by(StockMove.created_at.desc(), StockMove.id.desc()).limit(limit)
    res = await db.execute(stmt)
    moves = res.scalars().all()

    items = []
    for m in moves:
        items.append({
            "id": str(m.id),
            "date": m.created_at.strftime("%d/%m/%Y %H:%M") if m.created_at else "",
            "move_type": m.move_type.value if hasattr(m.move_type, "value") else str(m.move_type),
            "warehouse_id": str(m.warehouse_id) if m.warehouse_id else None,
            "warehouse_name": m.warehouse.name if m.warehouse else "Bodega Central",
            "qty": float(m.qty or 0),
            "qty_before": float(m.qty_before or 0),
            "qty_after": float(m.qty_after or 0),
            "unit_cost": float(m.unit_cost or 0),
            "reference": m.reference or "",
            "notes": m.notes or "",
        })

    # Calcular saldo acumulado neto del Kardex
    INFLOW_STRINGS = {"purchase", "transfer_in", "adj_in", "initial", "ret_sale"}
    all_moves_stmt = select(StockMove.move_type, StockMove.qty).where(StockMove.product_id == p_uuid)
    all_res = await db.execute(all_moves_stmt)
    kardex_balance = 0.0
    for m_type_obj, m_qty in all_res.all():
        m_type_str = m_type_obj.value if hasattr(m_type_obj, "value") else str(m_type_obj)
        if m_type_str in INFLOW_STRINGS:
            kardex_balance += float(m_qty or 0)
        else:
            kardex_balance -= float(m_qty or 0)

    # Stock actual en quants
    quant_stmt = select(func.sum(StockQuant.qty_on_hand)).where(StockQuant.product_id == p_uuid)
    quant_res = await db.execute(quant_stmt)
    current_stock = float(quant_res.scalar() or 0.0)

    has_discrepancy = abs(kardex_balance - current_stock) > 0.0001

    return {
        "items": items,
        "total": len(items),
        "kardex_balance": kardex_balance,
        "current_stock": current_stock,
        "has_discrepancy": has_discrepancy,
    }


@router.post("/{product_id}/sync-kardex")
@router.post("/{product_id}/sync-kardex-stock")
async def sync_product_stock_with_kardex(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Sincroniza y concilia las existencias de un producto (StockQuant) basándose estrictamente en su historial del Kardex."""
    check_product_management_permission(current_user)
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    prod_stmt = select(Product).where(Product.id == p_uuid, Product.company_id == current_user.company_id)
    prod_res = await db.execute(prod_stmt)
    prod = prod_res.scalar_one_or_none()
    if not prod:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    # Obtener bodegas válidas de la empresa
    wh_stmt = (
        select(Warehouse)
        .join(Branch, Warehouse.branch_id == Branch.id)
        .where(
            Branch.company_id == current_user.company_id,
            Warehouse.is_active == True,
            Warehouse.is_deleted == False
        )
        .order_by(Warehouse.is_default.desc(), Warehouse.created_at.asc())
    )
    wh_res = await db.execute(wh_stmt)
    valid_warehouses = wh_res.scalars().all()
    if not valid_warehouses:
        raise HTTPException(status_code=400, detail="No hay bodegas activas en la empresa")

    valid_wh_ids = {w.id for w in valid_warehouses}
    default_wh = valid_warehouses[0]

    # Movimientos ordenados cronológicamente
    moves_stmt = (
        select(StockMove)
        .where(StockMove.product_id == p_uuid)
        .order_by(StockMove.created_at.asc(), StockMove.id.asc())
    )
    moves_res = await db.execute(moves_stmt)
    all_moves = moves_res.scalars().all()

    # Reasignar bodegas huérfanas o erróneas a la bodega por defecto
    for m in all_moves:
        if m.warehouse_id not in valid_wh_ids:
            m.warehouse_id = default_wh.id

    INFLOW_STRINGS = {"purchase", "transfer_in", "adj_in", "initial", "ret_sale"}

    # Agrupar movimientos por bodega y recalcular la cadena continua qty_before / qty_after
    moves_by_wh = {}
    for m in all_moves:
        moves_by_wh.setdefault(m.warehouse_id, []).append(m)

    total_net_stock = Decimal("0")

    for wh in valid_warehouses:
        wh_moves = moves_by_wh.get(wh.id, [])
        running_stock = Decimal("0")

        for m in wh_moves:
            m_type_val = m.move_type.value if hasattr(m.move_type, "value") else str(m.move_type)
            is_in = m_type_val in INFLOW_STRINGS
            qty = Decimal(str(m.qty or 0))

            m.qty_before = running_stock
            if is_in:
                running_stock += qty
            else:
                running_stock -= qty
            m.qty_after = running_stock

        quant_stmt = select(StockQuant).where(
            StockQuant.product_id == p_uuid,
            StockQuant.warehouse_id == wh.id
        )
        quant_res = await db.execute(quant_stmt)
        quant = quant_res.scalar_one_or_none()

        if not quant:
            quant = StockQuant(
                product_id=p_uuid,
                warehouse_id=wh.id,
                qty_on_hand=running_stock,
                qty_reserved=Decimal("0"),
                qty_incoming=Decimal("0"),
                avg_cost=Decimal("0"),
            )
            db.add(quant)
        else:
            quant.qty_on_hand = running_stock

        total_net_stock += running_stock

    await db.commit()

    return {
        "success": True,
        "status": "success",
        "product_id": str(prod.id),
        "product_name": prod.name,
        "new_stock": float(total_net_stock),
        "total_stock": float(total_net_stock),
        "reconciled_stock": float(total_net_stock),
        "reconciled_moves_count": len(all_moves),
        "message": f"Stock conciliado exitosamente con el Kardex. Existencia actual: {float(total_net_stock):.2f} unidades."
    }



@router.post("/{product_id}/adjust-stock")
async def adjust_product_stock(
    product_id: str,
    data: StockAdjustIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ajustar el stock físico de un producto y registrar movimiento Kardex."""
    check_product_management_permission(current_user)
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    p_stmt = select(Product).where(Product.id == p_uuid, Product.company_id == current_user.company_id)
    p_res = await db.execute(p_stmt)
    product = p_res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    wh_uuid = None
    if data.warehouse_id:
        try:
            wh_uuid = uuid.UUID(data.warehouse_id)
        except ValueError:
            pass
    if not wh_uuid:
        wh_stmt = select(Warehouse).join(Branch).where(Branch.company_id == current_user.company_id).limit(1)
        wh_res = await db.execute(wh_stmt)
        wh = wh_res.scalar_one_or_none()
        wh_uuid = wh.id if wh else None
        if not wh_uuid:
            # Fallback to creating or finding default branch warehouse
            b_res = await db.execute(select(Branch).where(Branch.company_id == current_user.company_id).limit(1))
            branch = b_res.scalar_one_or_none()
            if branch:
                wh = Warehouse(branch_id=branch.id, name="Bodega Principal", code="BOD-001", is_default=True)
                db.add(wh)
                await db.flush()
                wh_uuid = wh.id
            else:
                wh_uuid = uuid.uuid4()

    quant_stmt = select(StockQuant).where(
        StockQuant.product_id == p_uuid,
        StockQuant.warehouse_id == wh_uuid
    )
    q_res = await db.execute(quant_stmt)
    quant = q_res.scalar_one_or_none()

    current_qty = quant.qty_on_hand if quant else Decimal("0")
    diff_qty = data.new_qty - current_qty

    if not quant:
        quant = StockQuant(
            warehouse_id=wh_uuid,
            product_id=p_uuid,
            qty_on_hand=data.new_qty,
            qty_reserved=Decimal("0")
        )
        db.add(quant)
    else:
        quant.qty_on_hand = data.new_qty

    # Crear movimiento Kardex
    move_type = StockMoveType.ADJUSTMENT_IN if diff_qty >= 0 else StockMoveType.ADJUSTMENT_OUT
    move = StockMove(
        warehouse_id=wh_uuid,
        product_id=p_uuid,
        qty=abs(diff_qty),
        qty_before=current_qty,
        qty_after=data.new_qty,
        unit_cost=product.cost_price or Decimal("0"),
        total_cost=abs(diff_qty) * (product.cost_price or Decimal("0")),
        move_type=move_type,
        reference=f"Ajuste: {data.reason}",
        notes=data.notes or f"Ajuste de inventario de {current_qty} a {data.new_qty}",
        user_id=current_user.id,
    )
    db.add(move)
    await db.commit()

    return {
        "product_id": str(p_uuid),
        "previous_qty": float(current_qty),
        "new_qty": float(data.new_qty),
        "difference": float(diff_qty),
        "status": "adjusted"
    }


@router.post("/")
async def create_product(
    data: ProductCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear un producto."""
    check_product_management_permission(current_user)
    category_uuid = uuid.UUID(data.category_id) if data.category_id else None
    supplier_uuid = uuid.UUID(data.supplier_id) if data.supplier_id else None
    uom_uuid = uuid.UUID(data.uom_id) if data.uom_id else None
    wholesale_uom_uuid = uuid.UUID(data.wholesale_uom_id) if data.wholesale_uom_id else None

    try:
        p_type = ProductType(data.product_type)
    except ValueError:
        p_type = ProductType.PRODUCT

    try:
        p_track = TrackingMethod(data.tracking)
    except ValueError:
        p_track = TrackingMethod.NONE

    internal_ref = (data.internal_ref or "").strip()
    if not internal_ref:
        internal_ref = await get_next_product_sku(db, current_user.company_id)

    product = Product(
        company_id=current_user.company_id,
        name=data.name,
        internal_ref=internal_ref,
        barcode=data.barcode,
        category_id=category_uuid,
        supplier_id=supplier_uuid,
        uom_id=uom_uuid,
        wholesale_price=data.wholesale_price or Decimal("0"),
        wholesale_uom_id=wholesale_uom_uuid,
        wholesale_ratio=data.wholesale_ratio or Decimal("1.0"),
        description=data.description,
        product_type=p_type,
        tracking=p_track,
        sale_price=data.sale_price,
        cost_price=data.cost_price,
        min_price=data.min_price,
        tax_rate=data.tax_rate,
        includes_tax=data.includes_tax,
        min_stock=data.min_stock,
        max_stock=data.max_stock,
        is_active=data.is_active,
        can_be_sold=data.can_be_sold,
        can_be_purchased=data.can_be_purchased,
        image_url=data.image_url,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return {"id": str(product.id), "name": product.name, "status": "created"}


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    data: ProductUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar un producto existente."""
    check_product_management_permission(current_user)
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    stmt = select(Product).where(Product.id == p_uuid, Product.company_id == current_user.company_id)
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if data.name is not None: p.name = data.name
    if data.internal_ref is not None: p.internal_ref = data.internal_ref
    if data.barcode is not None: p.barcode = data.barcode
    if data.category_id is not None: p.category_id = uuid.UUID(data.category_id) if data.category_id else None
    if data.supplier_id is not None: p.supplier_id = uuid.UUID(data.supplier_id) if data.supplier_id else None
    if data.uom_id is not None: p.uom_id = uuid.UUID(data.uom_id) if data.uom_id else None
    if data.wholesale_price is not None: p.wholesale_price = data.wholesale_price
    if data.wholesale_uom_id is not None: p.wholesale_uom_id = uuid.UUID(data.wholesale_uom_id) if data.wholesale_uom_id else None
    if data.wholesale_ratio is not None: p.wholesale_ratio = data.wholesale_ratio
    if data.description is not None: p.description = data.description
    if data.sale_price is not None: p.sale_price = data.sale_price
    if data.cost_price is not None: p.cost_price = data.cost_price
    if data.min_price is not None: p.min_price = data.min_price
    if data.min_stock is not None: p.min_stock = data.min_stock
    if data.max_stock is not None: p.max_stock = data.max_stock
    if data.tax_rate is not None: p.tax_rate = data.tax_rate
    if data.is_active is not None: p.is_active = data.is_active
    if data.image_url is not None: p.image_url = data.image_url

    await db.commit()
    return {"id": str(p.id), "status": "updated"}


@router.get("/{product_id}/barcode-label")
async def get_product_barcode_label(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generar etiqueta adhesiva con código de barras e info del producto."""
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    stmt = select(Product).where(Product.id == p_uuid, Product.company_id == current_user.company_id)
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    barcode_val = p.barcode or p.internal_ref or str(p.id)[:8]

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Etiqueta — {p.name}</title>
<style>
  @page {{ size: 50mm 30mm; margin: 0; }}
  body {{
    width: 48mm;
    height: 28mm;
    margin: 1mm auto;
    font-family: sans-serif;
    text-align: center;
    border: 1px dashed #ccc;
    box-sizing: border-box;
    padding: 2mm;
  }}
  .title {{ font-size: 10px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .price {{ font-size: 14px; font-weight: 900; color: #000; margin: 2px 0; }}
  .barcode-img {{ width: 100%; height: 28px; object-fit: contain; }}
  .code-text {{ font-size: 9px; font-family: monospace; letter-spacing: 1px; }}
  @media print {{ body {{ border: none; }} .no-print {{ display: none; }} }}
</style>
</head>
<body onload="window.print()">
  <div class="no-print" style="margin-bottom:4px"><button onclick="window.print()">🖨️ Imprimir Etiquetas</button></div>
  <div class="title">{p.name}</div>
  <div class="price">Q {float(p.sale_price or 0):.2f}</div>
  <img class="barcode-img" src="https://api.qrserver.com/v1/create-qr-code/?size=100x100&data={barcode_val}" alt="Barcode">
  <div class="code-text">{barcode_val}</div>
</body>
</html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html_content)


@router.post("/{product_id}/toggle-active")
async def toggle_product_active(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Archivar o desarchivar un producto (alternar estado activo/inactivo)."""
    check_product_management_permission(current_user)
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    stmt = select(Product).where(Product.id == p_uuid, Product.company_id == current_user.company_id)
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    p.is_active = not bool(p.is_active)
    await db.commit()
    await db.refresh(p)
    action_str = "activado" if p.is_active else "archivado"
    return {
        "id": str(p.id),
        "name": p.name,
        "is_active": p.is_active,
        "status": "unarchived" if p.is_active else "archived",
        "message": f"Producto '{p.name}' {action_str} correctamente."
    }


@router.delete("/{product_id}")
async def delete_product(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Eliminar un producto del catálogo (soft delete seguro)."""
    check_product_management_permission(current_user)
    from datetime import datetime, timezone
    try:
        p_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    stmt = select(Product).where(Product.id == p_uuid, Product.company_id == current_user.company_id)
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    p.is_deleted = True
    p.is_active = False
    p.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "id": str(p.id),
        "status": "deleted",
        "message": f"Producto '{p.name}' eliminado correctamente del inventario."
    }

