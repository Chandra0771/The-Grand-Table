/**
 * The Grand Table — Service Worker
 * Place this file at the ROOT of your frontend folder (same level as index.html).
 * Register it in index.html with the snippet shown at the bottom of this file.
 */

const CACHE_NAME = "grand-table-v1";

// Files to pre-cache on install (your app shell).
// Add any CSS / JS bundle filenames your frontend uses.
const PRECACHE_URLS = [
  "/",
  "/index.html",
  "/manifest.json",
  "/icons/icon-192x192.png",
  "/icons/icon-512x512.png",
];

// API routes we never cache — always go to the network.
const NETWORK_ONLY_PATTERNS = [
  /\/api\//,           // all backend API calls
  /\/api\/auth\//,     // auth endpoints
  /\/api\/orders\//,   // live order status
  /\/api\/payment\//,  // UPI payment endpoints
];

// ─────────────────────────────────────────────
// INSTALL — pre-cache the app shell
// ─────────────────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// ─────────────────────────────────────────────
// ACTIVATE — delete old caches
// ─────────────────────────────────────────────
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

// ─────────────────────────────────────────────
// FETCH — network-first for API, cache-first for assets
// ─────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests (POST, PUT, DELETE — let them pass through)
  if (request.method !== "GET") return;

  // Skip cross-origin requests
  if (url.origin !== self.location.origin) return;

  // Network-only for API routes (always fresh data)
  const isApiCall = NETWORK_ONLY_PATTERNS.some((pattern) =>
    pattern.test(url.pathname)
  );
  if (isApiCall) {
    event.respondWith(fetch(request));
    return;
  }

  // Cache-first with network fallback for everything else (app shell, icons, etc.)
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;

      return fetch(request)
        .then((networkResponse) => {
          // Cache successful responses for future offline use
          if (networkResponse && networkResponse.status === 200) {
            const cloned = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, cloned));
          }
          return networkResponse;
        })
        .catch(() => {
          // Offline fallback: serve index.html for navigation requests
          if (request.mode === "navigate") {
            return caches.match("/index.html");
          }
        });
    })
  );
});

// ─────────────────────────────────────────────
// PUSH NOTIFICATIONS (optional — wire up later)
// ─────────────────────────────────────────────
self.addEventListener("push", (event) => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || "The Grand Table", {
      body: data.body || "You have a new notification.",
      icon: "/icons/icon-192x192.png",
      badge: "/icons/icon-96x96.png",
      tag: data.tag || "grand-table-notif",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow(event.notification.data.url || "/")
  );
});

/* ═══════════════════════════════════════════════════════════════
   REGISTRATION SNIPPET
   Paste this inside a <script> tag at the bottom of index.html,
   just before </body>:

   <script>
     if ('serviceWorker' in navigator) {
       window.addEventListener('load', () => {
         navigator.serviceWorker.register('/service-worker.js')
           .then(reg  => console.log('SW registered:', reg.scope))
           .catch(err => console.error('SW registration failed:', err));
       });
     }
   </script>
   ═══════════════════════════════════════════════════════════════ */
