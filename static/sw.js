// EM OS service worker: runs in the background on the phone.
// - Caches the app's look (CSS, scripts, icons) so it opens instantly.
// - Shows a friendly offline screen when there's no internet.
// - NEVER caches your personal pages: those always come fresh from the server.
const CACHE = "emos-v1";
const ASSETS = [
  "/static/style.css",
  "/static/js/htmx.min.js",
  "/static/js/app.js",
  "/static/icons/icon-192.png",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Delete caches from older versions of the app
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    // Static files: answer from cache instantly, refresh the cache in the background
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        const fresh = fetch(request)
          .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          })
          .catch(() => cached);
        return cached || fresh;
      })
    );
  } else if (request.mode === "navigate") {
    // Pages: always from the network; offline screen if there's no connection
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html")));
  }
});
