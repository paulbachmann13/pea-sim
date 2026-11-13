// Service worker : met en cache la coquille de l'app (hors ligne). Les données viennent de Supabase,
// non interceptées ici. Changer VERSION (= APP_VERSION dans app.js) à chaque déploiement de l'app :
// le nouveau worker s'installe et la page propose « Mettre à jour ».
const VERSION = "1.5.0";
const CACHE = "pea-sim-" + VERSION;
const SHELL = ["./", "index.html", "style.css", "app.js", "manifest.webmanifest", "icon.svg", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" }))))
    // La v1.0 ne savait pas proposer de mise à jour : on la remplace d'office.
    .then(() => caches.has("pea-sim-v1")).then((legacy) => legacy && self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (e) => {
  if (e.data === "skipWaiting") self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then((r) => r || fetch(e.request)));
});
