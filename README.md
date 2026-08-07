# POS Empresarial 🏪

Sistema de **Punto de Venta Empresarial** de nivel Odoo, construido con Python (FastAPI) + PostgreSQL. Diseñado para abarrotes, farmacias, ferreterías y cualquier tipo de comercio con soporte para 20+ sucursales.

## 🚀 Stack Tecnológico

| Capa | Tecnología |
|------|-----------|
| Backend | FastAPI 0.115 (Python 3.12) |
| ORM | SQLAlchemy 2.0 (async) + Alembic |
| Base de Datos | PostgreSQL 16 |
| Cache / Colas | Redis 7 + Celery |
| Frontend | HTMX + Alpine.js + CSS puro |
| FEL | Interfaz genérica (INFILE/Megaprint/G4S) |
| PDFs | WeasyPrint |
| Contenedores | Docker + Docker Compose |

## 🎯 Módulos

- ✅ **Multi-sucursal** — 20+ sucursales con control de acceso por sucursal
- ✅ **POS Táctil** — Interfaz tipo Odoo con soporte offline
- ✅ **Inventario** — Kardex, stock por bodega, costo promedio, lotes, vencimientos
- ✅ **FEL Guatemala** — Facturación electrónica (INFILE/Megaprint/G4S/Genérico)
- ✅ **Caja** — Apertura/cierre, denominaciones, arqueo
- ✅ **Traslados** — Entre sucursales con aprobación y verificación
- ✅ **Compras** — Órdenes de compra y recepción parcial
- ✅ **Clientes/Proveedores** — CRM básico con NIT guatemalteco
- ✅ **Reportes** — Dashboard en tiempo real, PDF, Excel
- ✅ **Roles y Permisos** — Granulares por módulo y sucursal
- ✅ **WebSockets** — Notificaciones en tiempo real

## 📋 Inicio Rápido

### 1. Clonar y configurar

```bash
cd pos-empresarial
cp .env.example .env
# Editar .env con tus valores
```

### 2. Levantar con Docker

```bash
docker-compose up -d
```

### 3. Inicializar base de datos y datos de prueba

```bash
docker-compose exec backend python scripts/seed.py
```

### 4. Acceder

| URL | Descripción |
|-----|-------------|
| http://localhost | Sistema POS |
| http://localhost/api/docs | Documentación API |
| http://localhost/login | Login (admin@empresa.com / Admin2024!) |

## 💻 Desarrollo Local (sin Docker)

```bash
# Instalar dependencias
pip install -r backend/requirements.txt

# Variables de entorno
export DATABASE_URL="postgresql+asyncpg://pos_user:pos_pass@localhost:5432/pos_db"
export REDIS_URL="redis://localhost:6379/0"
export SECRET_KEY="mi-clave-secreta"

# Ejecutar migraciones
cd backend && alembic upgrade head

# Seed
python scripts/seed.py

# Servidor de desarrollo
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 🗂️ Estructura del Proyecto

```
pos-empresarial/
├── docker-compose.yml
├── .env.example
├── nginx/
│   └── nginx.conf
├── scripts/
│   ├── init.sql          # Extensiones PostgreSQL
│   └── seed.py           # Datos iniciales
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py           # FastAPI app
│       ├── core/             # Config, DB, Seguridad, Redis
│       ├── models/           # SQLAlchemy ORM
│       ├── schemas/          # Pydantic schemas
│       ├── api/v1/           # Endpoints REST
│       ├── services/         # Lógica de negocio
│       ├── workers/          # Celery tasks
│       └── templates/        # Jinja2 HTML
└── frontend/
    └── static/
        ├── css/main.css      # Design system completo
        └── js/api.js         # Cliente API + offline sync
```

## 🔑 Credenciales de Demo

| Usuario | Email | Password | Rol |
|---------|-------|----------|-----|
| Admin | admin@empresa.com | Admin2024! | Administrador |
| Cajero | cajero@empresa.com | Cajero2024! | Cajero |

## 🇬🇹 FEL Guatemala

Para conectar con un proveedor FEL real, configurar en `.env`:

```env
FEL_PROVIDER=infile     # infile | megaprint | g4s
FEL_API_URL=https://api.infile.com.gt
FEL_API_KEY=tu-api-key
FEL_SIGNER_USER=tu-usuario
FEL_SIGNER_PASS=tu-password
```

En modo `generic` (por defecto), los UUIDs son simulados para desarrollo.

## 🌐 WebSockets

El sistema usa WebSockets para notificaciones en tiempo real:

```javascript
const ws = new WebSocket(`ws://localhost/ws/${branch_id}`);
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // { type: 'low_stock', product: '...', qty: 5 }
};
```

## 📊 API REST

Documentación interactiva disponible en `/api/docs` (Swagger UI).

Principales endpoints:

```
POST   /api/v1/auth/login           — Iniciar sesión
GET    /api/v1/auth/me              — Usuario actual

GET    /api/v1/products/            — Listar productos
POST   /api/v1/products/            — Crear producto

GET    /api/v1/sales/               — Listar ventas
POST   /api/v1/sales/               — Crear venta (con FEL)
POST   /api/v1/sales/{id}/cancel    — Cancelar venta

GET    /api/v1/inventory/low-stock  — Productos stock bajo
GET    /api/v1/inventory/kardex     — Kardex de movimientos

GET    /api/v1/transfers/           — Traslados
POST   /api/v1/transfers/           — Crear traslado

POST   /api/v1/cashier/open         — Abrir caja
POST   /api/v1/cashier/close        — Cerrar caja

GET    /api/v1/reports/sales-chart  — Datos para gráficas
GET    /api/v1/dashboard/kpis       — KPIs del dashboard
```

## 🛡️ Seguridad

- JWT con refresh tokens (acceso 8h, refresh 30d)
- bcrypt para contraseñas
- Permisos granulares por módulo y sucursal
- Rate limiting en Nginx (30 req/s API, 5 req/min login)
- CORS configurado
- GZip compression

## 🔄 Próximas Funcionalidades

- [ ] App móvil (PWA completa con Service Worker)
- [ ] Módulo de RRHH / Nómina
- [ ] Contabilidad básica (Mayor, Balance)
- [ ] Integración con lectores de código de barras USB
- [ ] Impresión térmica directa (ESC/POS)
- [ ] Dashboard BI avanzado con filtros drill-down
