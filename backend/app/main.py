import sys
import os

# Auto-resolución de PYTHONPATH y env vars cuando el usuario ejecuta directamente app/main.py en el IDE
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_current_dir, "../"))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import jwt
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.database import check_db_connection
from app.core.redis import get_redis

# Importar todos los routers de la API
from app.api.v1 import (
    auth, branches, products, inventory, partners,
    cashier, sales, purchases, transfers, fel, reports, dashboard,
    hr, mfg, accounting, audit, ap, ar, quotes, delivery, users,
    website, forecast, presale
)

# Importar router de páginas HTML
from app.api import pages

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup y shutdown del servidor."""
    logger.info(f"🚀 Iniciando {settings.APP_NAME} v{settings.APP_VERSION}")

    # Verificar conexión a BD
    db_ok = await check_db_connection()
    if not db_ok:
        logger.error("❌ No se pudo conectar a la base de datos")

    # Verificar Redis
    try:
        r = await get_redis()
        await r.ping()
        logger.info("✅ Conexión a Redis establecida")
    except Exception as e:
        logger.warning(f"⚠️  Redis no disponible: {e}")

    logger.info("✅ Sistema POS listo para recibir solicitudes")
    yield

    logger.info("🔴 Cerrando el sistema POS...")


# Crear aplicación FastAPI
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
    Sistema de Punto de Venta Empresarial
    - Multi-sucursal (20+ sucursales)
    - Control de inventario con Kardex
    - Facturación FEL Guatemala
    - Traslados internos
    - Apertura/cierre de caja
    - Reportes y BI
    """,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ===== MIDDLEWARE =====
from app.core.audit_middleware import AuditLogMiddleware

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Cabeceras de seguridad HTTP estándar para mitigar ataques web comunes (OWASP)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.middleware("http")
async def web_page_auth_guard(request: Request, call_next):
    """Control de acceso para páginas web: redirige a /login si no hay sesión activa."""
    path = request.url.path

    # Prefijos públicos (API, estáticos, docs, PWA, health checks, tickets y cortes Z imprimibles)
    public_prefixes = (
        "/static",
        "/api",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/manifest.json",
        "/service-worker.js",
        "/favicon.ico",
        "/health",
        "/sales/",
        "/cashier/",
    )
    if any(path.startswith(prefix) for prefix in public_prefixes):
        return await call_next(request)

    # Validar sesión JWT mediante cookie access_token o cabecera Bearer
    token = request.cookies.get("access_token")
    if not token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:]

    is_authenticated = False
    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            if payload.get("type") == "access" and payload.get("sub"):
                is_authenticated = True
        except Exception:
            is_authenticated = False

    # Endpoint de cierre de sesión
    if path == "/logout":
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("access_token", path="/")
        return resp

    # Si accede a la página de login
    if path == "/login":
        if is_authenticated:
            return RedirectResponse(url="/dashboard", status_code=302)
        return await call_next(request)

    # Si accede a la raíz del sitio "/"
    if path == "/":
        if is_authenticated:
            return RedirectResponse(url="/dashboard", status_code=302)
        return RedirectResponse(url="/login", status_code=302)

    # Cualquier otra página protegida (/dashboard, /pos, /inventory, etc.)
    if not is_authenticated:
        query = str(request.url.query)
        next_target = path + (f"?{query}" if query else "")
        login_url = f"/login?next={next_target}" if next_target != "/dashboard" else "/login"
        resp = RedirectResponse(url=login_url, status_code=302)
        resp.delete_cookie("access_token", path="/")
        return resp

    return await call_next(request)

app.add_middleware(AuditLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ===== STATIC FILES =====
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, "../../"))

STATIC_DIR = os.path.join(PROJECT_ROOT, "frontend/static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ===== TEMPLATES =====
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ===== API ROUTERS (v1) =====
api_prefix = "/api/v1"
app.include_router(auth.router, prefix=api_prefix, tags=["Autenticación"])
app.include_router(branches.router, prefix=api_prefix, tags=["Sucursales"])
app.include_router(products.router, prefix=api_prefix, tags=["Productos"])
app.include_router(inventory.router, prefix=api_prefix, tags=["Inventario"])
app.include_router(partners.router, prefix=api_prefix, tags=["Clientes/Proveedores"])
app.include_router(cashier.router, prefix=api_prefix, tags=["Caja"])
app.include_router(sales.router, prefix=api_prefix, tags=["Ventas"])
app.include_router(purchases.router, prefix=api_prefix, tags=["Compras"])
app.include_router(transfers.router, prefix=api_prefix, tags=["Traslados"])
app.include_router(fel.router, prefix=api_prefix, tags=["FEL"])
app.include_router(reports.router, prefix=api_prefix, tags=["Reportes"])
app.include_router(dashboard.router, prefix=api_prefix, tags=["Dashboard"])

# Nuevos Módulos Odoo
app.include_router(hr.router, prefix=api_prefix, tags=["Recursos Humanos"])
app.include_router(mfg.router, prefix=api_prefix, tags=["Fabricación"])
app.include_router(accounting.router, prefix=api_prefix, tags=["Contabilidad"])
app.include_router(audit.router, prefix=api_prefix, tags=["Auditoría"])
app.include_router(ap.router, prefix=api_prefix, tags=["Cuentas por Pagar"])
app.include_router(ar.router, prefix=api_prefix, tags=["Cuentas por Cobrar"])
app.include_router(quotes.router, prefix=api_prefix, tags=["Cotizaciones"])
app.include_router(delivery.router, prefix=api_prefix, tags=["Entregas"])
app.include_router(users.router, prefix=api_prefix, tags=["Usuarios & Roles"])
app.include_router(website.router, prefix=api_prefix, tags=["Sitio Web & Landing"])
app.include_router(forecast.router, prefix=api_prefix, tags=["Previsión & Reorden"])
app.include_router(presale.router, prefix=api_prefix, tags=["Pre-venta & Solicitudes POS"])

# ===== PAGE ROUTERS (HTML) =====
app.include_router(pages.router, tags=["Páginas"])


# ===== WEBSOCKET para notificaciones en tiempo real =====
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, branch_id: str):
        await websocket.accept()
        if branch_id not in self.active_connections:
            self.active_connections[branch_id] = []
        self.active_connections[branch_id].append(websocket)

    def disconnect(self, websocket: WebSocket, branch_id: str):
        if branch_id in self.active_connections:
            if websocket in self.active_connections[branch_id]:
                self.active_connections[branch_id].remove(websocket)
            if not self.active_connections[branch_id]:
                del self.active_connections[branch_id]

    async def broadcast_to_branch(self, branch_id: str, message: dict):
        connections = self.active_connections.get(branch_id, [])
        for ws in connections[:]:
            try:
                await ws.send_json(message)
            except Exception:
                if ws in connections:
                    connections.remove(ws)
        if branch_id in self.active_connections and not self.active_connections[branch_id]:
            del self.active_connections[branch_id]


ws_manager = ConnectionManager()
app.state.ws_manager = ws_manager


@app.websocket("/ws/{branch_id}")
async def websocket_endpoint(websocket: WebSocket, branch_id: str):
    await ws_manager.connect(websocket, branch_id)
    try:
        while True:
            data = await websocket.receive_json()
            # Echo back + broadcast a la sucursal
            await ws_manager.broadcast_to_branch(branch_id, data)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, branch_id)


# ===== HEALTH CHECK =====
@app.get("/health", tags=["Sistema"])
@app.get("/api/v1/health", tags=["Sistema"])
async def health_check():
    from sqlalchemy import text
    from app.core.database import engine
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception as e:
        logger.warning(f"Health check DB ping: {e}")

    try:
        r = await get_redis()
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "not_configured",
    }


@app.get("/api/v1/info", tags=["Sistema"])
async def api_info():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


if __name__ == "__main__":
    import uvicorn
    print("🚀 Iniciando POS Empresarial en http://localhost:8000 ...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
