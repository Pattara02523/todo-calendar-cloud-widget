// Service Worker for Desktop To-Do & Google Calendar Widget (PWA Offline Support)
const CACHE_NAME = 'todo-calendar-widget-v2';
const APP_SHELL_ASSETS = [
  '/',
  '/index.html',
  '/desktop_todo_calendar_widget.html',
  '/manifest.json',
  '/sw.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(APP_SHELL_ASSETS).catch(() => {});
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Never intercept mutating requests (POST, PUT, PATCH, DELETE)
  if (req.method !== 'GET') {
    return;
  }

  const url = new URL(req.url);

  // Network-first with cache fallback for same-origin GET requests (app shell & read-only APIs)
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(req)
        .then((networkRes) => {
          if (networkRes && networkRes.status === 200) {
            const resClone = networkRes.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(req, resClone);
            });
          }
          return networkRes;
        })
        .catch(() => {
          return caches.match(req).then((cachedRes) => {
            if (cachedRes) return cachedRes;
            if (req.mode === 'navigate') {
              return caches.match('/desktop_todo_calendar_widget.html') || caches.match('/');
            }
            return new Response(
              JSON.stringify({ status: 'offline', todos: [], events: [] }),
              { status: 503, headers: { 'Content-Type': 'application/json; charset=utf-8' } }
            );
          });
        })
    );
  }
});
