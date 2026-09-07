"""Modelo para la configuración de la página web corporativa y landing page."""
from sqlalchemy import Column, String, Text, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
import copy
from app.models.base import BaseModel

DEFAULT_SECTORS = [
    {
        "id": "ferreteria",
        "name": "Ferreterías & Materiales",
        "icon": "ph-wrench",
        "badge": "Venta por Medidas & Granel",
        "image_url": "https://images.unsplash.com/photo-1581783342308-f792dbdd27c5?auto=format&fit=crop&w=800&q=80",
        "description": "Administración precisa para ferreterías, venta por yardas, metros, tornillos, combos de herramientas y cotizaciones inmediatas para proyectos de construcción.",
        "features": [
            "Venta por fracciones, metros y combos",
            "Cotizaciones con vigencia para constructoras",
            "Multi-bodega de materiales pesados",
            "Kardex con costo promedio ponderado"
        ]
    },
    {
        "id": "abarroteria",
        "name": "Abarroterías & Supermercados",
        "icon": "ph-shopping-cart",
        "badge": "Cobro Express en Cajas",
        "image_url": "https://images.unsplash.com/photo-1578916171728-46686eac8d58?auto=format&fit=crop&w=800&q=80",
        "description": "Agilidad absoluta en el mostrador: lectura de código de barras a máxima velocidad, conexión con balanzas, control de productos sueltos y alertas de stock mínimo.",
        "features": [
            "Lectura instantánea de códigos de barra",
            "Conexión a balanzas digitales y peso",
            "Cobro ultra rápido con atajos de teclado",
            "Alertas automáticas de existencias bajas"
        ]
    },
    {
        "id": "panaderia",
        "name": "Panaderías & Pastelerías",
        "icon": "ph-cookie",
        "badge": "Producción & Recetas BOM",
        "image_url": "https://images.unsplash.com/photo-1509440159596-0249088772ff?auto=format&fit=crop&w=800&q=80",
        "description": "Transforma harina, azúcar y materias primas en producto terminado con el módulo de manufactura y recetas, controlando mermas y despachando al mostrador al instante.",
        "features": [
            "Recetas (BOM) con descuento de insumos",
            "Costeo exacto por pieza de pan o pastel",
            "Venta en mostrador con pantalla táctil",
            "Control de turnos matutinos y vespertinos"
        ]
    },
    {
        "id": "restaurante",
        "name": "Restaurantes & Cafeterías",
        "icon": "ph-fork-knife",
        "badge": "Mesas & Comandas Touch",
        "image_url": "https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?auto=format&fit=crop&w=800&q=80",
        "description": "Control de consumo por mesas, comandas directas para barra o cocina, cuentas separadas, registro de propinas y administración de recetas e inventarios.",
        "features": [
            "Comandas rápidas para cocina o barra",
            "Cuentas divididas entre comensales",
            "Control de mesas activas y tiempos",
            "Propinas y múltiples métodos de pago"
        ]
    },
    {
        "id": "farmacia",
        "name": "Farmacias & Droguerías",
        "icon": "ph-first-aid",
        "badge": "Lotes & Vencimientos",
        "image_url": "https://images.unsplash.com/photo-1587854692152-cbe660dbde88?auto=format&fit=crop&w=800&q=80",
        "description": "Trazabilidad farmacéutica estricta: alertas de productos próximos a caducar, búsqueda por principio activo o marca comercial, y control de recetas médicas.",
        "features": [
            "Monitoreo de lotes y fechas de vencimiento",
            "Búsqueda por principio activo o marca",
            "Alertas de caducidad a 30, 60 y 90 días",
            "Facturación FEL SAT inmediata"
        ]
    },
    {
        "id": "boutique",
        "name": "Boutiques & Calzado",
        "icon": "ph-t-shirt",
        "badge": "Tallas, Colores & Variantes",
        "image_url": "https://images.unsplash.com/photo-1441986300917-64674bd600d8?auto=format&fit=crop&w=800&q=80",
        "description": "Gestión por matriz de variantes (talla, color, estilo), impresión de etiquetas de código de barras para prendas y aplicación de promociones y descuentos por temporada.",
        "features": [
            "Matriz de variantes por talla y color",
            "Impresión de etiquetas con código de barras",
            "Descuentos y promociones por temporada",
            "Cuentas por cobrar y crédito para clientes"
        ]
    }
]

DEFAULT_FEATURES = [
    {
        "id": "fel",
        "title": "Facturación Electrónica SAT (FEL)",
        "icon": "ph-receipt",
        "color": "#714B67",
        "description": "Emisión instantánea de Facturas, Notas de Crédito y Facturas Especiales con firma digital certificada, código QR y anulación en tiempo real."
    },
    {
        "id": "offline",
        "title": "Modo 100% Offline (Nunca se Detiene)",
        "icon": "ph-wifi-slash",
        "color": "#0ea5e9",
        "description": "Si se interrumpe la conexión a internet, el Punto de Venta continúa facturando con almacenamiento local en IndexedDB y se sincroniza al volver la red."
    },
    {
        "id": "kardex",
        "title": "Kardex 360° & Multi-Bodega",
        "icon": "ph-warehouse",
        "color": "#f97316",
        "description": "Seguimiento milimétrico de entradas, salidas, mermas y traslados físicos entre sucursales con cálculo de costo promedio ponderado."
    },
    {
        "id": "cartera",
        "title": "Cuentas por Cobrar & Pagar (AR / AP)",
        "icon": "ph-hand-coins",
        "color": "#10b981",
        "description": "Control de límites de crédito para clientes, reportes de antigüedad de saldos (aging report), estados de cuenta y abonos a proveedores."
    },
    {
        "id": "pos_touch",
        "title": "Terminal Touch & Atajos de Teclado",
        "icon": "ph-device-tablet",
        "color": "#8b5cf6",
        "description": "Interfaz intuitiva optimizada para pantallas táctiles, tablets y lectores de códigos de barra para un despacho sin filas ni esperas."
    },
    {
        "id": "bi_reports",
        "title": "Reportes Ejecutivos & Auditoría",
        "icon": "ph-chart-line-up",
        "color": "#ef4444",
        "description": "Métricas de rentabilidad en tiempo real, arqueos de caja Cierre Z/X, control de márgenes de utilidad y registro de auditoría para cada operación."
    }
]

DEFAULT_STATS = [
    {"value": "+500", "label": "Empresas en Producción"},
    {"value": "100%", "label": "Cumplimiento FEL SAT"},
    {"value": "99.9%", "label": "Disponibilidad Operativa"},
    {"value": "0 seg", "label": "Tiempo de Inactividad Offline"}
]


def get_default_website_dict() -> dict:
    """Retorna un diccionario completo con los valores por defecto de la web."""
    return {
        "site_title": "POS Empresarial ERP",
        "hero_badge": "⚡ Plataforma Comercial y Fiscal #1 en Guatemala",
        "hero_title": "El Sistema de Punto de Venta y ERP Todo en Uno para tu Negocio",
        "hero_subtitle": "Control integral de inventarios multi-bodega, facturación electrónica SAT (FEL), terminal touch ultra rápida y reportes financieros diseñados para hacer crecer tu empresa.",
        "hero_cta_login": "🔐 Iniciar Sesión",
        "hero_cta_demo": "💬 Solicitar Demostración",
        "hero_image_url": "https://images.unsplash.com/photo-1556742049-0a67c5574f73?auto=format&fit=crop&w=1200&q=80",
        "logo_url": "",
        "whatsapp_number": "50255551234",
        "whatsapp_message": "Hola, deseo información y una demostración del sistema POS Empresarial ERP.",
        "contact_phone": "+502 2200-0000",
        "contact_email": "ventas@pos.gt",
        "contact_address": "Ciudad de Guatemala, Guatemala C.A.",
        "footer_text": "© 2026 POS Empresarial ERP · Sistema Empresarial de Alto Rendimiento · Hecho en Guatemala.",
        "sectors": copy.deepcopy(DEFAULT_SECTORS),
        "features": copy.deepcopy(DEFAULT_FEATURES),
        "stats": copy.deepcopy(DEFAULT_STATS),
        "is_active": True
    }


class WebsiteConfig(BaseModel):
    """Configuración y contenidos de la página web corporativa / landing page."""
    __tablename__ = "website_configs"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True, index=True)
    site_title = Column(String(200), default="POS Empresarial ERP", nullable=False)
    hero_badge = Column(String(200), default="⚡ Plataforma Comercial y Fiscal #1 en Guatemala")
    hero_title = Column(String(300), default="El Sistema de Punto de Venta y ERP Todo en Uno para tu Negocio", nullable=False)
    hero_subtitle = Column(Text)
    hero_cta_login = Column(String(100), default="🔐 Iniciar Sesión")
    hero_cta_demo = Column(String(100), default="💬 Solicitar Demostración")
    hero_image_url = Column(String(500))
    logo_url = Column(String(500))
    whatsapp_number = Column(String(50), default="50255551234")
    whatsapp_message = Column(Text, default="Hola, deseo información y una demostración del sistema POS Empresarial ERP.")
    contact_phone = Column(String(50), default="+502 2200-0000")
    contact_email = Column(String(150), default="ventas@pos.gt")
    contact_address = Column(String(250), default="Ciudad de Guatemala, Guatemala C.A.")
    footer_text = Column(String(300))
    
    # Secciones dinámicas en JSONB
    sectors = Column(JSONB, default=lambda: copy.deepcopy(DEFAULT_SECTORS))
    features = Column(JSONB, default=lambda: copy.deepcopy(DEFAULT_FEATURES))
    stats = Column(JSONB, default=lambda: copy.deepcopy(DEFAULT_STATS))
    gallery_images = Column(JSONB, default=list)

    is_active = Column(Boolean, default=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "site_title": self.site_title or "POS Empresarial ERP",
            "hero_badge": self.hero_badge or "⚡ Plataforma Comercial y Fiscal #1 en Guatemala",
            "hero_title": self.hero_title or "El Sistema de Punto de Venta y ERP Todo en Uno para tu Negocio",
            "hero_subtitle": self.hero_subtitle or "",
            "hero_cta_login": self.hero_cta_login or "🔐 Iniciar Sesión",
            "hero_cta_demo": self.hero_cta_demo or "💬 Solicitar Demostración",
            "hero_image_url": self.hero_image_url or "https://images.unsplash.com/photo-1556742049-0a67c5574f73?auto=format&fit=crop&w=1200&q=80",
            "logo_url": self.logo_url or "",
            "whatsapp_number": self.whatsapp_number or "50255551234",
            "whatsapp_message": self.whatsapp_message or "Hola, deseo información y una demostración del sistema POS Empresarial ERP.",
            "contact_phone": self.contact_phone or "+502 2200-0000",
            "contact_email": self.contact_email or "ventas@pos.gt",
            "contact_address": self.contact_address or "Ciudad de Guatemala, Guatemala C.A.",
            "footer_text": self.footer_text or "© 2026 POS Empresarial ERP · Sistema Empresarial de Alto Rendimiento · Hecho en Guatemala.",
            "sectors": self.sectors or copy.deepcopy(DEFAULT_SECTORS),
            "features": self.features or copy.deepcopy(DEFAULT_FEATURES),
            "stats": self.stats or copy.deepcopy(DEFAULT_STATS),
            "gallery_images": self.gallery_images or [],
            "is_active": self.is_active if self.is_active is not None else True
        }
