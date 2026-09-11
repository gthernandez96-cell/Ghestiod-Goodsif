/**
 * POS IndexedDB & Bidirectional Offline Sync Engine
 * POS Empresarial — v2.0
 */

class POSIndexedDB {
  constructor() {
    this.dbName = 'PosEmpresarialDB';
    this.version = 2;
    this.db = null;
    this.isSyncing = false;
    this.syncListeners = [];
  }

  /**
   * Inicializar conexión a IndexedDB y crear almacenes si no existen
   */
  async init() {
    if (this.db) return this.db;

    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.dbName, this.version);

      request.onupgradeneeded = (event) => {
        const db = event.target.result;

        // 1. Productos
        if (!db.objectStoreNames.contains('products')) {
          const prodStore = db.createObjectStore('products', { keyPath: 'id' });
          prodStore.createIndex('barcode', 'barcode', { unique: false });
          prodStore.createIndex('name', 'name', { unique: false });
          prodStore.createIndex('category_id', 'category_id', { unique: false });
        }

        // 2. Categorías
        if (!db.objectStoreNames.contains('categories')) {
          const catStore = db.createObjectStore('categories', { keyPath: 'id' });
          catStore.createIndex('name', 'name', { unique: false });
        }

        // 3. Clientes
        if (!db.objectStoreNames.contains('customers')) {
          const custStore = db.createObjectStore('customers', { keyPath: 'id' });
          custStore.createIndex('nit', 'nit', { unique: false });
          custStore.createIndex('name', 'name', { unique: false });
        }

        // 4. Ventas Offline
        if (!db.objectStoreNames.contains('offline_sales')) {
          const salesStore = db.createObjectStore('offline_sales', { keyPath: 'client_offline_id' });
          salesStore.createIndex('status', 'status', { unique: false });
          salesStore.createIndex('created_at', 'created_at', { unique: false });
        }

        // 5. Metadatos
        if (!db.objectStoreNames.contains('metadata')) {
          db.createObjectStore('metadata', { keyPath: 'key' });
        }
      };

      request.onsuccess = (event) => {
        this.db = event.target.result;
        resolve(this.db);
      };

      request.onerror = (event) => {
        console.error('Error al abrir IndexedDB:', event.target.error);
        reject(event.target.error);
      };
    });
  }

  // ===== HELPERS DE TRANSACCIÓN =====
  async _tx(storeNames, mode = 'readonly') {
    await this.init();
    return this.db.transaction(storeNames, mode);
  }

  async _getAll(storeName) {
    const tx = await this._tx(storeName, 'readonly');
    const store = tx.objectStore(storeName);
    return new Promise((resolve, reject) => {
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }

  async _get(storeName, key) {
    const tx = await this._tx(storeName, 'readonly');
    const store = tx.objectStore(storeName);
    return new Promise((resolve, reject) => {
      const req = store.get(key);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  }

  async _put(storeName, item) {
    const tx = await this._tx(storeName, 'readwrite');
    const store = tx.objectStore(storeName);
    return new Promise((resolve, reject) => {
      const req = store.put(item);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async _setMeta(key, value) {
    return this._put('metadata', { key, value, updated_at: new Date().toISOString() });
  }

  async _getMeta(key) {
    const res = await this._get('metadata', key);
    return res ? res.value : null;
  }

  // ===== GESTIÓN DE PRODUCTOS & CATEGORÍAS =====

  async saveProducts(products) {
    const tx = await this._tx('products', 'readwrite');
    const store = tx.objectStore('products');
    // Limpiar catálogo local previo para purgar productos archivados o eliminados
    await new Promise((res, rej) => {
      const req = store.clear();
      req.onsuccess = () => res();
      req.onerror = () => rej(req.error);
    });
    for (const p of products) {
      if (p.is_active !== false && !p.is_deleted) {
        store.put(p);
      }
    }
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve(products.length);
      tx.onerror = () => reject(tx.error);
    });
  }

  async getAllProducts() {
    const all = await this._getAll('products');
    return (all || []).filter(p => p.is_active !== false && !p.is_deleted);
  }

  async getProductById(id) {
    const p = await this._get('products', id);
    if (p && (p.is_active === false || p.is_deleted)) return null;
    return p;
  }

  async getProductByBarcode(barcode) {
    if (!barcode) return null;
    const cleanCode = String(barcode).trim();
    const products = await this.getAllProducts();
    return products.find(p =>
      (p.barcode && String(p.barcode).trim() === cleanCode) ||
      (p.internal_ref && String(p.internal_ref).trim() === cleanCode)
    ) || null;
  }

  async searchProducts(term = '', categoryId = null) {
    const all = await this.getAllProducts();
    let filtered = all.filter(p => p.is_active !== false && !p.is_deleted);

    if (categoryId && categoryId !== 'all') {
      filtered = filtered.filter(p => String(p.category_id) === String(categoryId));
    }

    if (term && term.trim()) {
      const q = term.trim().toLowerCase();
      filtered = filtered.filter(p =>
        (p.name && p.name.toLowerCase().includes(q)) ||
        (p.barcode && p.barcode.toLowerCase().includes(q)) ||
        (p.internal_ref && p.internal_ref.toLowerCase().includes(q))
      );
    }

    return filtered;
  }

  async updateProductStock(productId, qtySold) {
    const tx = await this._tx('products', 'readwrite');
    const store = tx.objectStore('products');
    const p = await new Promise((res, rej) => {
      const req = store.get(productId);
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });

    if (p) {
      const currentStock = Number(p.stock || 0);
      p.stock = Math.max(0, currentStock - Number(qtySold || 0));
      store.put(p);
    }
  }

  async updateStockMap(stockMap) {
    if (!stockMap || typeof stockMap !== 'object') return;
    try {
      const tx = await this._tx('products', 'readwrite');
      const store = tx.objectStore('products');
      const all = await this._getAll('products');
      for (const p of (all || [])) {
        if (p && p.id && stockMap[p.id] !== undefined) {
          const newStk = Number(stockMap[p.id] || 0);
          if (p.stock !== newStk) {
            p.stock = newStk;
            store.put(p);
          }
        }
      }
    } catch(e) {
      console.warn("Error actualizando stock masivo en IndexedDB:", e);
    }
  }

  async saveCategories(categories) {
    const tx = await this._tx('categories', 'readwrite');
    const store = tx.objectStore('categories');
    for (const c of categories) {
      store.put(c);
    }
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve(categories.length);
      tx.onerror = () => reject(tx.error);
    });
  }

  async getAllCategories() {
    return this._getAll('categories');
  }

  // ===== GESTIÓN DE CLIENTES =====

  async saveCustomers(customers) {
    const tx = await this._tx('customers', 'readwrite');
    const store = tx.objectStore('customers');
    for (const c of customers) {
      store.put(c);
    }
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve(customers.length);
      tx.onerror = () => reject(tx.error);
    });
  }

  async getAllCustomers() {
    return this._getAll('customers');
  }

  async searchCustomers(term = '') {
    const all = await this.getAllCustomers();
    if (!term || !term.trim()) return all.slice(0, 20);

    const q = term.trim().toLowerCase();
    return all.filter(c =>
      (c.nit && c.nit.toLowerCase().includes(q)) ||
      (c.name && c.name.toLowerCase().includes(q)) ||
      (c.phone && c.phone.includes(q))
    ).slice(0, 20);
  }

  async saveCustomer(customer) {
    if (!customer.id) {
      customer.id = 'local_cust_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5);
    }
    await this._put('customers', customer);
    return customer;
  }

  // ===== GESTIÓN DE VENTAS OFFLINE =====

  async saveOfflineSale(salePayload) {
    // Generar ID único para la venta offline
    const clientOfflineId = 'off_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    const offlineNumber = 'POS-OFF-' + new Date().toISOString().replace(/\D/g, '').substr(2, 12);

    const record = {
      client_offline_id: clientOfflineId,
      offline_number: offlineNumber,
      payload: {
        ...salePayload,
        client_offline_id: clientOfflineId,
      },
      status: 'pending', // 'pending' | 'synced' | 'failed'
      total: salePayload.lines ? salePayload.lines.reduce((sum, l) => sum + (Number(l.qty) * Number(l.unit_price)), 0) : 0,
      customer_name: salePayload.customer_name || 'CONSUMIDOR FINAL',
      customer_nit: salePayload.customer_nit || 'CF',
      created_at: new Date().toISOString(),
      synced_at: null,
      server_id: null,
      fel_uuid: null,
      error_message: null,
    };

    // 1. Guardar en IndexedDB
    await this._put('offline_sales', record);

    // 2. Descontar stock localmente en IndexedDB para cada línea vendida
    if (salePayload.lines && Array.isArray(salePayload.lines)) {
      for (const line of salePayload.lines) {
        if (line.product_id) {
          await this.updateProductStock(line.product_id, line.qty);
        }
      }
    }

    this._notifyListeners('sale_saved', record);
    return record;
  }

  async getPendingSales() {
    const all = await this._getAll('offline_sales');
    return all.filter(s => s.status === 'pending' || s.status === 'failed');
  }

  async getAllSales() {
    const all = await this._getAll('offline_sales');
    return all.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  }

  async markSaleSynced(clientOfflineId, serverResult) {
    const sale = await this._get('offline_sales', clientOfflineId);
    if (!sale) return;

    sale.status = 'synced';
    sale.synced_at = new Date().toISOString();
    sale.server_id = serverResult.id;
    sale.server_number = serverResult.number;
    sale.fel_uuid = serverResult.fel_uuid;
    sale.error_message = null;

    await this._put('offline_sales', sale);
    this._notifyListeners('sale_synced', sale);
    return sale;
  }

  async markSaleFailed(clientOfflineId, errorMsg) {
    const sale = await this._get('offline_sales', clientOfflineId);
    if (!sale) return;

    sale.status = 'failed';
    sale.error_message = String(errorMsg || 'Error desconocido');
    await this._put('offline_sales', sale);
    this._notifyListeners('sale_failed', sale);
  }

  // ===== ESTADÍSTICAS & METADATOS =====

  async getSyncStats() {
    const [products, categories, customers, allSales, lastCatSync, lastCustSync] = await Promise.all([
      this.getAllProducts(),
      this.getAllCategories(),
      this.getAllCustomers(),
      this.getAllSales(),
      this._getMeta('last_catalog_sync'),
      this._getMeta('last_customer_sync'),
    ]);

    const pendingSales = allSales.filter(s => s.status === 'pending' || s.status === 'failed');
    const syncedSales = allSales.filter(s => s.status === 'synced');

    return {
      totalProducts: products.length,
      totalCategories: categories.length,
      totalCustomers: customers.length,
      pendingSalesCount: pendingSales.length,
      pendingSalesList: pendingSales,
      syncedSalesCount: syncedSales.length,
      lastCatalogSync: lastCatSync,
      lastCustomerSync: lastCustSync,
    };
  }

  // ===== MOTOR DE SINCRONIZACIÓN BIDIRECCIONAL =====

  async syncCatalogFromServer(apiClient) {
    try {
      const whId = localStorage.getItem('warehouse_id') || '';
      let url = '/products/pos-catalog';
      if (whId) {
        url += `?warehouse_id=${encodeURIComponent(whId)}`;
      }
      const data = await apiClient.get(url);
      if (data && data.products) {
        await this.saveProducts(data.products);
        if (data.categories) {
          await this.saveCategories(data.categories);
        }
        await this._setMeta('last_catalog_sync', new Date().toISOString());
        this._notifyListeners('catalog_synced', { count: data.products.length });
        return { success: true, count: data.products.length };
      }
      return { success: false, error: 'Respuesta inválida del servidor' };
    } catch (e) {
      console.warn('No se pudo sincronizar el catálogo desde el servidor:', e);
      return { success: false, error: e.message };
    }
  }

  async syncCustomersFromServer(apiClient) {
    try {
      const data = await apiClient.get('/partners/?partner_type=customer&limit=500');
      const items = Array.isArray(data) ? data : (data.items || []);
      if (items.length > 0) {
        await this.saveCustomers(items);
        await this._setMeta('last_customer_sync', new Date().toISOString());
        this._notifyListeners('customers_synced', { count: items.length });
        return { success: true, count: items.length };
      }
      return { success: true, count: 0 };
    } catch (e) {
      console.warn('No se pudo sincronizar clientes desde el servidor:', e);
      return { success: false, error: e.message };
    }
  }

  async syncPendingSalesToServer(apiClient) {
    if (this.isSyncing) return { inProgress: true };
    this.isSyncing = true;
    this._notifyListeners('sync_started');

    const pending = await this.getPendingSales();
    let syncedCount = 0;
    let failedCount = 0;
    const errors = [];

    for (const record of pending) {
      try {
        const payload = record.payload;
        const result = await apiClient.post('/sales/', payload);
        await this.markSaleSynced(record.client_offline_id, result);
        syncedCount++;
      } catch (err) {
        console.error(`Error al sincronizar venta offline ${record.client_offline_id}:`, err);
        await this.markSaleFailed(record.client_offline_id, err.message);
        failedCount++;
        errors.push({ id: record.client_offline_id, error: err.message });
      }
    }

    this.isSyncing = false;
    this._notifyListeners('sync_finished', { syncedCount, failedCount, errors });

    return { syncedCount, failedCount, errors };
  }

  // ===== LISTENERS =====
  onSyncEvent(callback) {
    this.syncListeners.push(callback);
  }

  _notifyListeners(event, data = {}) {
    for (const cb of this.syncListeners) {
      try { cb(event, data); } catch(e) { console.error('Error en listener sync:', e); }
    }
  }
}

// Instancia global
window.posDB = new POSIndexedDB();
