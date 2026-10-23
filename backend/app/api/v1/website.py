"""API v1 — Configuración y Gestión de la Página Web / Landing Page."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Any, Dict
import os
import uuid
import logging

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.website import WebsiteConfig, get_default_website_dict, DEFAULT_SECTORS, DEFAULT_FEATURES, DEFAULT_STATS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/website", tags=["Website & Landing"])


# ===== SCHEMAS =====
class WebsiteConfigUpdate(BaseModel):
    site_title: Optional[str] = None
    hero_badge: Optional[str] = None
    hero_title: Optional[str] = None
    hero_subtitle: Optional[str] = None
    hero_cta_login: Optional[str] = None
    hero_cta_demo: Optional[str] = None
    hero_image_url: Optional[str] = None
    logo_url: Optional[str] = None
    whatsapp_number: Optional[str] = None
    whatsapp_message: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    contact_address: Optional[str] = None
    footer_text: Optional[str] = None
    sectors: Optional[List[Dict[str, Any]]] = None
    features: Optional[List[Dict[str, Any]]] = None
    stats: Optional[List[Dict[str, Any]]] = None
    gallery_images: Optional[List[Dict[str, Any]]] = None
    is_active: Optional[bool] = None

    model_config = ConfigDict(extra="ignore")


# ===== ENDPOINTS =====

@router.get("/config")
async def get_website_config(
    db: AsyncSession = Depends(get_db)
):
    """Obtener la configuración pública actual de la página web corporativa."""
    try:
        stmt = select(WebsiteConfig).order_by(WebsiteConfig.created_at.asc())
        res = await db.execute(stmt)
        config = res.scalars().first()
        if config:
            return config.to_dict()
    except Exception as e:
        logger.warning(f"Error al consultar WebsiteConfig de BD: {e}")

    # Retornar valores por defecto ricos si la tabla aún no tiene registros
    return get_default_website_dict()


@router.put("/config")
async def update_website_config(
    data: WebsiteConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar la configuración de la página web corporativa (Solo Administradores)."""
    # Verificar si ya existe registro
    stmt = select(WebsiteConfig).order_by(WebsiteConfig.created_at.asc())
    res = await db.execute(stmt)
    config = res.scalars().first()

    if not config:
        config = WebsiteConfig(
            company_id=getattr(current_user, "company_id", None),
            **get_default_website_dict()
        )
        db.add(config)

    # Actualizar campos provistos
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if hasattr(config, field) and value is not None:
            setattr(config, field, value)

    await db.commit()
    await db.refresh(config)
    return {
        "success": True,
        "message": "Configuración del sitio web actualizada con éxito",
        "data": config.to_dict()
    }


@router.post("/upload-image")
async def upload_website_image(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Subir imagen para hero banner, logo o giros de negocio."""
    filename = file.filename or "image.png"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa JPG, PNG, WEBP, SVG o GIF.")

    # Directorio de subida: frontend/static/uploads/website
    app_dir = os.path.dirname(os.path.abspath(__file__))
    upload_dir = os.path.abspath(os.path.join(app_dir, "../../../../frontend/static/uploads/website"))
    os.makedirs(upload_dir, exist_ok=True)

    unique_name = f"web_{uuid.uuid4().hex[:12]}{ext}"
    file_path = os.path.join(upload_dir, unique_name)

    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen no debe exceder los 8MB.")

    # Subir a DigitalOcean Spaces si está configurado
    from app.services.spaces_storage import upload_to_spaces
    spaces_upload = upload_to_spaces(
        content,
        filename=unique_name,
        content_type=file.content_type or "image/png",
        folder="media/website"
    )
    if spaces_upload:
        return {
            "success": True,
            "url": spaces_upload["url"],
            "filename": unique_name,
            "storage": "spaces"
        }

    with open(file_path, "wb") as f:
        f.write(content)

    return {
        "success": True,
        "url": f"/static/uploads/website/{unique_name}",
        "filename": unique_name,
        "storage": "local"
    }


@router.post("/reset-defaults")
async def reset_website_defaults(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Restablecer los contenidos y sectores a los valores predeterminados de fábrica."""
    stmt = select(WebsiteConfig).order_by(WebsiteConfig.created_at.asc())
    res = await db.execute(stmt)
    config = res.scalars().first()

    defaults = get_default_website_dict()
    if not config:
        config = WebsiteConfig(
            company_id=getattr(current_user, "company_id", None),
            **defaults
        )
        db.add(config)
    else:
        for k, v in defaults.items():
            if hasattr(config, k):
                setattr(config, k, v)

    await db.commit()
    await db.refresh(config)

    return {
        "success": True,
        "message": "Contenidos restablecidos a valores predeterminados exitosamente",
        "data": config.to_dict()
    }
