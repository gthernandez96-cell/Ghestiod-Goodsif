"""API v1 — Facturación Electrónica FEL Guatemala."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.fel import FELDocument, FELStatus
from app.models.sale import SaleOrder
from app.services.fel_service import FELService

router = APIRouter(prefix="/fel")


@router.get("/documents")
async def list_fel_documents(
    branch_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar documentos DTE certificados/anulados."""
    stmt = select(FELDocument).order_by(FELDocument.certified_at.desc()).limit(limit)

    if branch_id:
        try:
            stmt = stmt.where(FELDocument.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    docs = res.scalars().all()

    return [
        {
            "id": str(d.id),
            "uuid": d.uuid,
            "series": d.series,
            "number": d.number,
            "dte_type": d.dte_type,
            "receiver_nit": d.receiver_nit,
            "receiver_name": d.receiver_name,
            "total": float(d.total or 0),
            "state": d.status.value if hasattr(d.status, 'value') else str(d.status),
            "certified_at": d.certified_at.isoformat() if d.certified_at else None,
        }
        for d in docs
    ]


@router.get("/documents/{document_id}/xml")
async def get_fel_xml(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener el archivo XML firmado del documento DTE."""
    try:
        d_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de documento inválido")

    stmt = select(FELDocument).where(FELDocument.id == d_uuid)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()

    if not doc or not doc.xml_signed:
        raise HTTPException(status_code=404, detail="XML no disponible")

    return Response(content=doc.xml_signed, media_type="application/xml")


@router.post("/certify/{sale_id}")
async def certify_sale(
    sale_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Solicitar la certificación FEL de una venta pendiente."""
    try:
        s_uuid = uuid.UUID(sale_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de venta inválido")

    stmt = select(SaleOrder).where(SaleOrder.id == s_uuid)
    res = await db.execute(stmt)
    sale = res.scalar_one_or_none()

    if not sale:
        raise HTTPException(status_code=404, detail="Venta no encontrada")

    fel_service = FELService(db)
    result = await fel_service.certify_sale(sale)
    await db.commit()

    return result


from app.services.fel_service import lookup_nit_sat


@router.get("/lookup-nit/{nit}")
async def lookup_nit(
    nit: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Consulta de NIT SAT Guatemala para auto-completar Razón Social y Dirección en emisión FEL."""
    nit_raw = nit.strip()
    clean_nit = nit_raw.upper().replace(" ", "").replace("-", "")

    if not clean_nit or clean_nit in ["CF", "CONSUMIDORFINAL", "C/F"]:
        return {
            "found": True,
            "nit": "CF",
            "name": "CONSUMIDOR FINAL",
            "trade_name": "Consumidor Final",
            "address": "CIUDAD DE GUATEMALA",
            "valid": True,
            "source": "cf",
        }

    # 1. Buscar en base de datos local
    from app.models.partner import Partner
    stmt = select(Partner).where(
        Partner.company_id == current_user.company_id,
        or_(
            Partner.nit.ilike(nit_raw),
            Partner.nit.ilike(clean_nit)
        )
    ).limit(1)
    res = await db.execute(stmt)
    partner = res.scalar_one_or_none()

    if partner:
        return {
            "found": True,
            "nit": partner.nit,
            "name": partner.name,
            "trade_name": partner.trade_name or partner.name,
            "address": partner.address or "CIUDAD DE GUATEMALA",
            "valid": True,
            "source": "local_database",
        }

    # 2. Consultar ante SAT con Módulo 11
    sat_res = await lookup_nit_sat(nit_raw)
    return sat_res

