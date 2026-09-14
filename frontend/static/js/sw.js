const CACHE_NAME = 'pos-empresarial-cache-v15';
const ASSETS_TO_CACHE = [
  '/login',
  '/dashboard',
  '/pos',
  '/static/css/main.css?v=20260906_hide_uom_normal',
  '/static/js/app.js',
  '/static/js/api.js',
  '/static/js/db.js',
];

// Install: pre-cache critical assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    }).then(() => self.skipWaiting())
  );
});

// Activate: clear old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            return caches.delete(cache);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch interceptor
self.addEventListener('fetch', (event) => {
  const requestUrl = new URL(event.request.url);

  // Bypass service worker caching for API calls to guarantee fresh database reads/writes
  if (requestUrl.pathname.startsWith('/api/v1/')) {
    return;
  }

  // Network-First strategy for pages to ensure dynamic updates, falling back to cache if offline
  if (
    event.request.mode === 'navigate' ||
    ASSETS_TO_CACHE.some(asset => requestUrl.pathname === asset || requestUrl.pathname + requestUrl.search === asset)
  ) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Cache clone of successful response
          if (response.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() => {
          // Fallback to cache when offline
          return caches.match(event.request);
        })
    );
    return;
  }

  // Cache-First strategy for secondary static assets (css, js, images, icons)
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(event.request).then((response) => {
        if (response.status === 200) {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return response;
      });
    })
  );
});
