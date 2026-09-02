"""Middleware para Auditoría Automática de Seguridad."""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt
import uuid
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.audit import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Permitir que transcurra la petición primero
        response = await call_next(request)

        # Solo registrar operaciones mutables (POST, PUT, PATCH, DELETE)
        if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            path = request.url.path

            # Omitir endpoints de autenticación y de desarrollo interno que no requieren auditoría
            if any(p in path for p in ["/auth/login", "/auth/refresh", "/health"]):
                return response

            # Si la respuesta no es exitosa (ej. 4xx o 5xx), omitir o registrar como advertencia
            status_code = response.status_code
            state = "valid"
            if status_code >= 400:
                state = "warning"

            # Intentar decodificar el usuario desde el token JWT
            user_id = None
            company_id = None
            auth_header = request.headers.get("Authorization")

            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                try:
                    payload = jwt.decode(
                        token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
                    )
                    sub = payload.get("sub")
                    if sub:
                        user_id = uuid.UUID(str(sub))
                except Exception:
                    pass

            # Obtener IP del cliente
            ip_address = request.client.host if request.client else "127.0.0.1"

            # Crear sesión para guardar en BD de forma asíncrona e independiente
            async with AsyncSessionLocal() as session:
                try:
                    # Si obtuvimos user_id, buscar su company_id
                    if user_id:
                        from sqlalchemy import select
                        stmt = select(User).where(User.id == user_id).limit(1)
                        res = await session.execute(stmt)
                        user = res.scalar_one_or_none()
                        if user:
                            company_id = user.company_id

                    # Si no hay company_id (ej. no autenticado o error), obtener el primero por defecto
                    if not company_id:
                        from app.models.company import Company
                        res_c = await session.execute(select(Company.id).limit(1))
                        company_id = res_c.scalar_one_or_none()

                    if company_id:
                        action_desc = f"Solicitud {request.method} a {path} (Código: {status_code})"
                        # Si es una ruta particular, dar descripción más clara
                        if "/api/v1/products" in path:
                            action_desc = f"Modificación de catálogo de productos: {request.method} {path}"
                        elif "/api/v1/sales" in path:
                            action_desc = f"Operación de Ventas / POS: {request.method} {path}"
                        elif "/api/v1/hr" in path:
                            action_desc = f"Gestión de personal de RRHH: {request.method} {path}"
                        elif "/api/v1/mfg" in path:
                            action_desc = f"Movimiento de Fabricación / Horneado: {request.method} {path}"

                        log = AuditLog(
                            company_id=company_id,
                            user_id=user_id,
                            timestamp=datetime.now(timezone.utc),
                            module=path.split("/")[3].upper() if len(path.split("/")) > 3 else "SISTEMA",
                            action=action_desc,
                            ip_address=ip_address,
                            state=state
                        )
                        session.add(log)
                        await session.commit()
                except Exception as e:
                    logger.error(f"Error guardando bitácora de auditoría en middleware: {e}")

        return response
