/**
 * API Client — POS Empresarial
 * Helper para llamadas a la API REST
 */
const API = {
  baseUrl: '/api/v1',

  _getHeaders() {
    const token = localStorage.getItem('access_token');
    return {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    };
  },

  async _request(method, endpoint, body = null) {
    let url = this.baseUrl + endpoint;
    const opts = {
      method,
      headers: this._getHeaders(),
    };
    if (method === 'GET') {
      opts.cache = 'no-store';
      opts.headers['Cache-Control'] = 'no-cache';
      opts.headers['Pragma'] = 'no-cache';
    }
    if (body) opts.body = JSON.stringify(body);

    const resp = await fetch(url, opts);

    if (resp.status === 401) {
      const refreshed = await this._refreshToken();
      if (refreshed) {
        opts.headers = this._getHeaders();
        const retry = await fetch(url, opts);
        if (!retry.ok) throw new Error(`HTTP ${retry.status}`);
        return retry.json();
      } else {
        localStorage.clear();
        document.cookie = "access_token=; path=/; max-age=0; SameSite=Lax";
        window.location.href = '/login';
        return;
      }
    }

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      let msg = err.detail || `HTTP ${resp.status}`;
      if (Array.isArray(msg)) {
        msg = msg.map(m => m.msg || (m.loc ? `${m.loc.join('.')}: ${m.type}` : JSON.stringify(m))).join(', ');
      } else if (typeof msg === 'object') {
        msg = JSON.stringify(msg);
      }
      throw new Error(msg);
    }

    if (resp.status === 204) return null;
    return resp.json();
  },

  async _refreshToken() {
    const refresh = localStorage.getItem('refresh_token');
    if (!refresh) return false;
    try {
      const resp = await fetch(this.baseUrl + '/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!resp.ok) return false;
      const data = await resp.json();
      localStorage.setItem('access_token', data.access_token);
      document.cookie = `access_token=${data.access_token}; path=/; max-age=86400; SameSite=Lax`;
      return true;
    } catch { return false; }
  },

  get: (endpoint) => API._request('GET', endpoint),
  post: (endpoint, body) => API._request('POST', endpoint, body),
  put: (endpoint, body) => API._request('PUT', endpoint, body),
  patch: (endpoint, body) => API._request('PATCH', endpoint, body),
  delete: (endpoint) => API._request('DELETE', endpoint),
};

/**
 * Toast notifications
 */
function showToast(message, type = 'info', duration = 4000) {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.style.cssText = 'position:fixed; bottom:24px; right:24px; z-index:99999; display:flex; flex-direction:column; gap:8px; pointer-events:none;';
    document.body.appendChild(container);
  }

  const icons = { success: 'ph-check-circle', error: 'ph-x-circle', warning: 'ph-warning', info: 'ph-info' };

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.style.pointerEvents = 'auto';
  toast.innerHTML = `
    <i class="ph ${icons[type] || icons.info}" style="font-size:18px;flex-shrink:0"></i>
    <span>${message}</span>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.animation = 'toastOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// Global alias for compatibility
window.toast = showToast;
window.showToast = showToast;

/**
 * Format currency
 */
function formatMoney(value, currency = 'GTQ') {
  return `Q ${Number(value || 0).toLocaleString('es-GT', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * Offline sync
 */
async function syncOfflineSales() {
  // If we are on the POS screen, let the POS page's specific UI-aware syncing logic handle it
  if (window.location.pathname.includes('/pos')) return;

  const offlineSales = JSON.parse(localStorage.getItem('pos_offline_sales') || '[]');
  if (offlineSales.length === 0 || !navigator.onLine) return;

  let synced = 0;
  const remaining = [];

  for (const sale of offlineSales) {
    try {
      await API.post('/sales/', sale);
      synced++;
    } catch {
      remaining.push(sale);
    }
  }

  localStorage.setItem('pos_offline_sales', JSON.stringify(remaining));

  if (synced > 0) {
    showToast(`${synced} venta(s) sincronizada(s) con el servidor`, 'success');
  }
}

// Sincronizar cuando recupere conexión
window.addEventListener('online', syncOfflineSales);

// Agregar animación de salida para toasts
const style = document.createElement('style');
style.textContent = `
  @keyframes toastOut {
    to { opacity: 0; transform: translateX(20px); }
  }
`;
document.head.appendChild(style);
