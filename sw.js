// Service worker do OndeToBus.
//
// Estratégia:
//  - "app shell" (HTML, CSS, JS, ícones, manifest): cache-first com
//    atualização em segundo plano (stale-while-revalidate), assim o app
//    abre instantâneo mesmo com internet ruim e ainda se mantém atualizado.
//  - /api/*: sempre busca da rede (dados de ônibus em tempo real não
//    devem vir de cache — informação desatualizada seria pior que nenhuma).
//
// Sempre que mudar o CSS/JS/HTML do app, troque o CACHE_NAME (ex.: v2, v3...)
// pra forçar os usuários a baixarem a versão nova.
const CACHE_NAME = "ondetobus-v1";

const APP_SHELL = [
  "/",
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

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

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== "GET") return;

  // Chamadas de API: sempre rede, nunca cache (dados têm que ser atuais).
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request));
    return;
  }

  // Recursos externos (fontes, Leaflet via unpkg): deixa o navegador cuidar.
  if (url.origin !== self.location.origin) return;

  // App shell: stale-while-revalidate.
  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(request);
      const networkFetch = fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            cache.put(request, response.clone());
          }
          return response;
        })
        .catch(() => cached);

      return cached || networkFetch;
    })
  );
});
