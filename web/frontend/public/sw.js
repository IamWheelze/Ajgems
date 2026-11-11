// FR-095: lets the field page reopen with no connection. App files are cached as they load; the few API
// reads the field page needs are network-first with a cached fallback. Writes are never cached: the
// page queues visits in IndexedDB and syncs them itself.
const CACHE = "minefoot-v1";
const OFFLINE_API = ["/api/me/", "/api/field/tasks/", "/api/review-options/"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.add("/"))); self.skipWaiting(); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

const networkFirst = async (req, fallbackKey) => {
  const c = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) c.put(fallbackKey || req, res.clone());
    return res;
  } catch {
    return (await c.match(fallbackKey || req)) || Response.error();
  }
};

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    if (OFFLINE_API.includes(url.pathname)) e.respondWith(networkFirst(req));
    return;   // other API reads go to the network only
  }
  if (req.mode === "navigate") { e.respondWith(networkFirst(req, "/")); return; }
  e.respondWith(caches.open(CACHE).then(async (c) => (await c.match(req)) || fetch(req).then((res) => {
    if (res.ok) c.put(req, res.clone());
    return res;
  })));
});
