// EM OS service worker: runs in the background on the phone.
// - Caches the app's look (CSS, scripts, icons) so it opens instantly.
// - Shows a friendly offline screen when there's no internet.
// - NEVER caches your personal pages: those always come fresh from the server.
// - Shows the daily reminder notifications, even when the app is closed.
const CACHE = "emos-v6";
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

// Daily reminders: the server sends a push message, we show it as a notification
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "EM OS", {
      body: data.body || "",
      icon: "/static/icons/icon-192.png",
      tag: data.tag || "emos",          // a new reminder replaces the old one
      data: { url: data.url || "/" },
    })
  );
});

// Tapping the notification opens EM OS on the right page
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = (event.notification.data && event.notification.data.url) || "/";
  const url = new URL(path, self.location.origin).href;   // only ever our own site
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const win of windows) {
        if (win.url.startsWith(self.location.origin) && "focus" in win) {
          return win.focus().then((w) => (w && "navigate" in w ? w.navigate(url) : w));
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
