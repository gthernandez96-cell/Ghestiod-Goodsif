#!/bin/bash
# ==============================================================================
# Script de Despliegue Rápido a DigitalOcean (POS Empresarial)
# ==============================================================================
set -e

DROPLET_IP="161.35.4.11"
REMOTE_PATH="/opt/pos-empresarial"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=================================================="
echo "🚀 Sincronizando cambios a DigitalOcean (${DROPLET_IP})..."
echo "=================================================="

# 1. Sincronizar archivos usando rsync (solo transfiere los archivos que cambiaron)
# IMPORTANTE: Se excluye pos.db para preservar las ventas y datos de producción
rsync -avz --progress \
  --exclude '.git' \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude 'scratch' \
  --exclude '.system_generated' \
  --exclude '.env' \
  --exclude 'pos.db' \
  --exclude 'pos.db-journal' \
  --exclude 'backend/pos.db' \
  --exclude 'backend/pos.db-journal' \
  -e "ssh -o StrictHostKeyChecking=no" \
  "$PROJECT_ROOT/" "root@${DROPLET_IP}:${REMOTE_PATH}/"

echo "=================================================="
echo "🔄 Reiniciando servicio pos-empresarial..."
echo "=================================================="

ssh -o StrictHostKeyChecking=no "root@${DROPLET_IP}" << 'ENDSSH'
# Si hay nuevos paquetes, instalarlos
/opt/pos-empresarial/venv/bin/pip install -r /opt/pos-empresarial/backend/requirements.txt aiosqlite --quiet

# Reiniciar servicio
systemctl restart pos-empresarial
sleep 4
systemctl is-active --quiet pos-empresarial && echo "✅ Servicio POS activo y funcionando" || (echo "❌ Error en el servicio" && exit 1)

# Verificar respuesta local
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -L http://127.0.0.1:8000/login)
echo "✅ Estado HTTP /login: $HTTP_CODE"
ENDSSH

echo "=================================================="
echo "🎉 ¡Actualización desplegada exitosamente!"
echo "📍 URL: https://pos.${DROPLET_IP}.sslip.io"
echo "=================================================="
