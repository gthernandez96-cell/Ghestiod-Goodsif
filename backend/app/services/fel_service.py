import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_current_dir, "../../"))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID
import json
import logging
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.fel import FELDocument, FELSequence, DTEType, FELStatus
from app.models.sale import SaleOrder
from app.models.company import Branch, Company

logger = logging.getLogger(__name__)


class FELError(Exception):
    def __init__(self, message: str, code: str = "FEL_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class FELService:
    """Servicio genérico de FEL — adaptable a cualquier proveedor."""

    def __init__(self, db, company_id: UUID):
        self.db = db
        self.company_id = company_id
        self.provider = settings.FEL_PROVIDER  # infile | megaprint | g4s | generic

    async def generate_dte(self, sale: SaleOrder) -> FELDocument:
        """Generar y certificar un DTE para una venta."""
        if sale.branch_id:
            res = await self.db.execute(select(Branch).options(selectinload(Branch.company)).where(Branch.id == sale.branch_id))
            sale.branch = res.scalar_one_or_none()

        # Construir el XML del DTE
        xml_content = self._build_xml(sale)

        # Certificar con el proveedor
        try:
            result = await self._certify(xml_content, sale)
            status = FELStatus.CERTIFIED
        except FELError as e:
            logger.error(f"Error FEL: {e.message}")
            # Guardar en contingencia
            result = {
                "uuid": None,
                "authorization_number": None,
                "series": None,
                "number": None,
            }
            status = FELStatus.CONTINGENCY

        # Crear el documento FEL
        doc = FELDocument(
            company_id=self.company_id,
            branch_id=sale.branch_id,
            sale_id=sale.id,
            dte_type=DTEType.INVOICE,
            status=status,
            issuer_nit=sale.branch.company.nit if (sale.branch and sale.branch.company) else "",
            issuer_name=sale.branch.company.name if (sale.branch and sale.branch.company) else "",
            establishment_code=sale.branch.fel_establishment_code if sale.branch else "1",
            issuer_address=sale.branch.fel_address if sale.branch else "",
            receiver_nit=sale.customer_nit or "CF",
            receiver_name=sale.customer_name or "CONSUMIDOR FINAL",
            receiver_address=sale.customer_address or "",
            internal_number=sale.number,
            uuid=result.get("uuid"),
            authorization_number=result.get("authorization_number"),
            series=result.get("series"),
            number=result.get("number"),
            certified_at=datetime.now(timezone.utc) if status == FELStatus.CERTIFIED else None,
            subtotal=sale.subtotal,
            tax_amount=sale.tax_amount,
            total=sale.total,
            xml_content=xml_content,
            provider_response=result,
        )

        self.db.add(doc)
        await self.db.flush()

        return doc

    def _build_xml(self, sale: SaleOrder) -> str:
        """Construir XML del DTE según formato SAT Guatemala."""
        now = datetime.now(timezone.utc)

        # Namespace SAT Guatemala (formato genérico FEL)
        root = ET.Element("dte:GTDocumento")
        root.set("xmlns:dte", "http://www.sat.gob.gt/dte/fel/0.2.0")
        root.set("Version", "0.1")

        # SAT
        sat = ET.SubElement(root, "dte:SAT")
        sat.set("ClaseDocumento", "dte")

        # DTE
        dte = ET.SubElement(sat, "dte:DTE")
        dte.set("ID", "DatosCertificados")

        datos = ET.SubElement(dte, "dte:DatosEmision")
        datos.set("ID", "DatosEmision")

        # Datos Generales
        dg = ET.SubElement(datos, "dte:DatosGenerales")
        dg.set("CodigoMoneda", "GTQ")
        dg.set("FechaHoraEmision", now.strftime("%Y-%m-%dT%H:%M:%S-06:00"))
        dg.set("Tipo", "FACT")

        # Emisor
        emisor = ET.SubElement(datos, "dte:Emisor")
        emisor.set("AfiliacionIVA", "GEN")  # GEN o PEQ
        emisor.set("CodigoEstablecimiento", (sale.branch.fel_establishment_code if sale.branch else None) or "1")
        emisor.set("CorreoEmisor", (sale.branch.email if sale.branch else None) or "")
        emisor.set("NITEmisor", sale.branch.company.nit if (sale.branch and sale.branch.company) else "")
        emisor.set("NombreComercial", (sale.branch.name if sale.branch else None) or "")
        emisor.set("NombreEmisor", sale.branch.company.name if (sale.branch and sale.branch.company) else "")

        direccion_emisor = ET.SubElement(emisor, "dte:DireccionEmisor")
        ET.SubElement(direccion_emisor, "dte:Direccion").text = (sale.branch.address if sale.branch else None) or "Ciudad"
        ET.SubElement(direccion_emisor, "dte:CodigoPostal").text = "01001"
        ET.SubElement(direccion_emisor, "dte:Municipio").text = (sale.branch.city if sale.branch else None) or "Guatemala"
        ET.SubElement(direccion_emisor, "dte:Departamento").text = "Guatemala"
        ET.SubElement(direccion_emisor, "dte:Pais").text = "GT"

        # Receptor
        receptor = ET.SubElement(datos, "dte:Receptor")
        receptor.set("IDReceptor", sale.customer_nit or "CF")
        receptor.set("NombreReceptor", sale.customer_name or "CONSUMIDOR FINAL")
        receptor.set("CorreoReceptor", "")

        # Frases (IVA incluido)
        frases = ET.SubElement(datos, "dte:Frases")
        frase = ET.SubElement(frases, "dte:Frase")
        frase.set("CodigoEscenario", "1")
        frase.set("TipoFrase", "1")

        # Items
        items = ET.SubElement(datos, "dte:Items")
        for i, line in enumerate(sale.lines, 1):
            item = ET.SubElement(items, "dte:Item")
            item.set("BienOServicio", "B")
            item.set("NumeroLinea", str(i))

            ET.SubElement(item, "dte:Cantidad").text = str(line.qty)
            ET.SubElement(item, "dte:UnidadMedida").text = "UNI"
            ET.SubElement(item, "dte:Descripcion").text = line.product_name[:200]
            ET.SubElement(item, "dte:PrecioUnitario").text = str(line.unit_price)
            ET.SubElement(item, "dte:Precio").text = str(line.subtotal)
            ET.SubElement(item, "dte:Descuento").text = str(line.discount_amount)

            impuestos = ET.SubElement(item, "dte:Impuestos")
            impuesto = ET.SubElement(impuestos, "dte:Impuesto")
            ET.SubElement(impuesto, "dte:NombreCorto").text = "IVA"
            ET.SubElement(impuesto, "dte:CodigoUnidadGravable").text = "1"
            ET.SubElement(impuesto, "dte:MontoGravable").text = str(line.subtotal)
            ET.SubElement(impuesto, "dte:MontoImpuesto").text = str(line.tax_amount)

            ET.SubElement(item, "dte:Total").text = str(line.total)

        # Totales
        totales = ET.SubElement(datos, "dte:Totales")
        total_impuestos = ET.SubElement(totales, "dte:TotalImpuestos")
        total_iva = ET.SubElement(total_impuestos, "dte:TotalImpuesto")
        total_iva.set("NombreCorto", "IVA")
        total_iva.set("TotalMontoImpuesto", str(sale.tax_amount))
        ET.SubElement(totales, "dte:GranTotal").text = str(sale.total)

        return ET.tostring(root, encoding="unicode", xml_declaration=False)

    async def _certify(self, xml_content: str, sale: SaleOrder) -> dict:
        """Enviar DTE al proveedor FEL para certificación."""
        if self.provider == "infile":
            return await self._certify_infile(xml_content)
        elif self.provider == "megaprint":
            return await self._certify_megaprint(xml_content)
        elif self.provider == "g4s":
            return await self._certify_g4s(xml_content)
        else:
            # Modo genérico / simulación
            return await self._certify_generic(xml_content, sale)

    async def _certify_generic(self, xml_content: str, sale: SaleOrder) -> dict:
        """Certificación genérica — para desarrollo/demo."""
        import uuid
        fake_uuid = str(uuid.uuid4()).upper()
        logger.info(f"📄 FEL Genérico — UUID asignado: {fake_uuid}")
        return {
            "uuid": fake_uuid,
            "authorization_number": f"AUTH-{fake_uuid[:8]}",
            "series": "A",
            "number": sale.number,
            "certified_at": datetime.now(timezone.utc).isoformat(),
            "provider": "generic",
        }

    async def _certify_infile(self, xml_content: str) -> dict:
        """Certificación con INFILE."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.FEL_API_URL}/certificate",
                headers={
                    "usuario": settings.FEL_SIGNER_USER,
                    "llave": settings.FEL_SIGNER_PASS,
                    "identificador": settings.FEL_CERTIFICATE_ID,
                    "Content-Type": "application/xml",
                },
                content=xml_content.encode("utf-8"),
            )
            if resp.status_code != 200:
                raise FELError(f"INFILE error {resp.status_code}: {resp.text}")
            data = resp.json()
            return {
                "uuid": data.get("uuid"),
                "authorization_number": data.get("numeroAutorizacion"),
                "series": data.get("serie"),
                "number": data.get("numero"),
                "provider": "infile",
            }

    async def _certify_megaprint(self, xml_content: str) -> dict:
        """Certificación con Megaprint."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.FEL_API_URL}/certificar",
                json={
                    "nit_emisor": settings.FEL_SIGNER_USER,
                    "llave_firma": settings.FEL_SIGNER_PASS,
                    "datos_dte": xml_content,
                },
            )
            if resp.status_code != 200:
                raise FELError(f"Megaprint error {resp.status_code}: {resp.text}")
            data = resp.json()
            return {
                "uuid": data.get("uuid"),
                "authorization_number": data.get("numeroAutorizacion"),
                "series": data.get("serie"),
                "number": data.get("numero"),
                "provider": "megaprint",
            }

    async def _certify_g4s(self, xml_content: str) -> dict:
        """Certificación con G4S/Digifact."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.FEL_API_URL}/api/dte",
                headers={"Authorization": f"Bearer {settings.FEL_API_KEY}"},
                json={"xml": xml_content},
            )
            if resp.status_code != 200:
                raise FELError(f"G4S error {resp.status_code}: {resp.text}")
            data = resp.json()
            return {
                "uuid": data.get("uuid"),
                "authorization_number": data.get("autorizacion"),
                "series": data.get("serie"),
                "number": data.get("numero"),
                "provider": "g4s",
            }

    async def cancel_dte(self, fel_doc: FELDocument, reason: str) -> bool:
        """Anular un DTE ante el SAT."""
        if not fel_doc.uuid:
            raise FELError("No se puede anular un DTE sin UUID")

        try:
            if self.provider == "infile":
                ok = await self._cancel_infile(fel_doc.uuid, reason)
            else:
                # Genérico: siempre ok en dev
                ok = True
                logger.info(f"FEL Genérico — anulación simulada: {fel_doc.uuid}")

            if ok:
                fel_doc.status = FELStatus.CANCELLED
                await self.db.flush()

            return ok
        except Exception as e:
            raise FELError(f"Error al anular DTE: {str(e)}")

    async def _cancel_infile(self, uuid: str, reason: str) -> bool:
        """Anular DTE en INFILE."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.FEL_API_URL}/anular",
                headers={
                    "usuario": settings.FEL_SIGNER_USER,
                    "llave": settings.FEL_SIGNER_PASS,
                },
                json={"uuid": uuid, "motivo": reason},
            )
            return resp.status_code == 200

    async def retry_pending(self, company_id: UUID) -> int:
        """Reintentar documentos en contingencia."""
        from sqlalchemy import select, and_
        stmt = select(FELDocument).where(
            and_(
                FELDocument.company_id == company_id,
                FELDocument.status == FELStatus.CONTINGENCY,
                FELDocument.retry_count < 5,
            )
        )
        result = await self.db.execute(stmt)
        docs = result.scalars().all()
        synced = 0
        for doc in docs:
            try:
                cert_result = await self._certify(doc.xml_content, None)
                doc.uuid = cert_result.get("uuid")
                doc.authorization_number = cert_result.get("authorization_number")
                doc.series = cert_result.get("series")
                doc.number = cert_result.get("number")
                doc.status = FELStatus.CERTIFIED
                doc.certified_at = datetime.now(timezone.utc)
                doc.synced_at = datetime.now(timezone.utc)
                synced += 1
            except Exception as e:
                doc.retry_count += 1
                doc.error_message = str(e)

        await self.db.flush()
        return synced
