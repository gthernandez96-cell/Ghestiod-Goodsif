"""Aplicación principal FastAPI — POS Empresarial."""
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import logging
import os

from app.core.config import settings
from app.core.database import check_db_connection
from app.core.redis import get_redis

# Importar todos los routers de la API
from app.api.v1 import (
    auth, branches, products, inventory, partners,
    cashier, sales, purchases, transfers, fel, reports, dashboard
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ===== STATIC FILES =====
STATIC_DIR = "/app/static" if os.path.exists("/app/static") else "../../frontend/static"
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ===== TEMPLATES =====
TEMPLATES_DIR = "/app/app/templates" if os.path.exists("/app/app/templates") else "app/templates"
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
            self.active_connections[branch_id].remove(websocket)

    async def broadcast_to_branch(self, branch_id: str, message: dict):
        connections = self.active_connections.get(branch_id, [])
        for ws in connections[:]:
            try:
                await ws.send_json(message)
            except Exception:
                connections.remove(ws)


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
async def health_check():
    db_ok = await check_db_connection()
    try:
        r = await get_redis()
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    return {
        "status": "ok" if (db_ok and redis_ok) else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }


@app.get("/api/v1/info", tags=["Sistema"])
async def api_info():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }
